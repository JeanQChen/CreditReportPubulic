# -*- coding: utf-8 -*-
"""TS5 的**版本化、公司无关**结构分类与 cell 内角色规则（计划 §19.6.3 / §19.6.4）。

本模块只有两个职责，且都**只读**：

1. 读入并逐字节校验两份只读资产
   （`policies/table_classification_profile_v1.json`、`policies/table_cell_block_profile_v1.json`）
   与它们的注册表（`policies/table_profile_registry_v1.json`）；
2. 用这两份资产把「outline path + 结构位置」映射成 `structure_class`，把
   「cell 内字体/编号/换行/相对 bbox/相邻块」映射成 `cell block role`。

设计边界（全部为强制）：

- **分类器不读 V2 TODO、不读 gold、不读公司名/证券代码/页码/表号/Evidence ID**。
  允许输入只有：已验证 outline path（真实标题，不含 synopsis）、source boundary、
  table 相对 node 的结构位置、profile 内容与 normalization version（§19.6.4）。
- 两份 profile 各自有独立真值表；**未知字段、未知版本、字节漂移、指纹漂移一律
  fail-closed**。
- 资产的**身份**是"canonical 内容指纹"；文件身份是 raw bytes SHA256。两者都
  进入 `TableObjectV4` 上游依赖束，所以任何模式、优先级或 normalization 改动都会
  改变 table revision identity。
- `structure_class` **只是结构标签**，永不授予数字 authority；`unclassified` 不是
  失败态，而是"证据不足"的诚实结论。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    identity,
    locator,
    sha256_canonical,
)
from document_structure.schema import (
    _err,
    _need_children,
    _need_enum,
    _need_int,
    _need_num,
    _need_schema_version,
    _need_sha256,
    _need_str,
    _need_str_tuple,
    _reject_unknown,
)
from document_structure.span_schema import _check_own_schema_version
from document_structure.table_schema import TABLES_STRUCTURE_CLASSES

__all__ = [
    "TABLE_PROFILE_REGISTRY_VERSION", "PROFILE_KINDS", "PROFILES_DIR_NAME",
    "NORMALIZATION_RULE_KINDS", "PATTERN_KINDS", "BLOCK_SIGNAL_NAMES",
    "CELL_BLOCK_RULE_KEYS", "STRUCTURE_CLASS_RATIONALE_CODES",
    "CELL_BLOCK_ROLE_RATIONALE_CODES",
    "CLASSIFICATION_PRIORITY_WHEN_CODES",
    "TitleNormalizationRule", "SubjectStripRule", "HeadingPattern",
    "ClassificationPriorityRow", "LineGroupRule", "FontSignals",
    "CellBlockRule", "CellBlockSignals",
    "TableClassificationProfile", "TableCellBlockProfile",
    "TableProfileBundle", "TableClassificationInputs",
    "load_table_profile_bundle", "load_classification_profile",
    "load_cell_block_profile", "profiles_directory",
    "normalize_heading_title", "heading_match_candidates",
    "classify_structure_class", "classify_cell_block_role",
    "evaluate_cell_block_signals", "self_check", "_main",
]

#: 注册表自身的**文件格式**版本。它与 profile 版本是三条独立轴：
#: 注册表格式（`tpr-1`）/ 分类 profile（`tcp-1`）/ cell block profile（`tcbp-1`）。
TABLE_PROFILE_REGISTRY_VERSION = "tpr-1"

#: profile 种类（封闭）。
PROFILE_KINDS: tuple[str, ...] = ("classification", "cell_block")

#: 两份只读资产与注册表所在目录名（相对 `document_structure/`）。
PROFILES_DIR_NAME = "policies"
PROFILE_REGISTRY_FILE = "table_profile_registry_v1.json"

#: 标题归一化规则种类（封闭）。未知种类 fail-closed，不得静默跳过。
NORMALIZATION_RULE_KINDS: tuple[str, ...] = (
    "strip_whitespace", "regex_sub", "strip_chars",
)

#: 标题模式匹配代数（封闭）。profile 只能在这三种里声明。
PATTERN_KINDS: tuple[str, ...] = (
    "contains_any", "contains_all", "contains_all_with_any",
)

#: §19.6.4 五条优先级行的 `when` 码（封闭，顺序固定）。
CLASSIFICATION_PRIORITY_WHEN_CODES: tuple[str, ...] = (
    "owner_or_path_or_boundary_missing_or_unresolvable_conflict",
    "ancestor_path_matches_note_subtree_patterns",
    "outside_notes_subtree_and_nearest_heading_matches_main_statement_patterns",
    "inside_verified_body_node_or_boundary_and_none_above",
    "otherwise",
)

#: cell block 规则键（封闭）。它们同时是 §19.6.2 的 role 词表来源。
CELL_BLOCK_RULE_KEYS: tuple[str, ...] = (
    "heading", "list_item", "paragraph", "line", "unclassified",
)

#: cell block 分类器**允许出现**的信号名（封闭）。profile 声明未登记信号名即失败。
BLOCK_SIGNAL_NAMES: tuple[str, ...] = (
    # 结构信号（由 line group 与相邻块给出）
    "own_line_group", "single_line_group", "line_group_count_at_least_2",
    "followed_by_other_block_in_same_cell", "unclosed_inputs",
    # 文本信号
    "numbering_marker_prefix", "starts_with_list_marker", "has_sentence_terminator",
    "no_sentence_terminator", "wrap_continuation",
    # 字体信号
    "size_ratio_at_least_heading_min", "bold_span_majority",
)

#: `structure_class` 的裁决码（封闭）。它不是分类标签，而是"凭什么这样分"。
STRUCTURE_CLASS_RATIONALE_CODES: tuple[str, ...] = (
    "owner_path_unavailable",
    "ancestor_path_note_pattern",
    "nearest_heading_main_statement_pattern",
    "inside_verified_body",
    "no_rule_matched",
    "pattern_conflict",
)

#: cell block role 的裁决码（封闭）。
CELL_BLOCK_ROLE_RATIONALE_CODES: tuple[str, ...] = (
    "rule_heading", "rule_list_item", "rule_paragraph", "rule_line",
    "rule_unclassified", "unclosed_inputs", "rule_conflict",
)

#: profile 中**禁止出现**的字面形状（机械反硬编码门）。任何一条命中即 fail-closed。
_FORBIDDEN_PROFILE_TOKEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[0-9]{6}", "六位数字串（证券代码形状）"),
    (r"(?i)\b(?:gold|evidence|evb|node|loc|to[0-9]|sc)-", "内部身份前缀"),
    (r"^[0-9]+$", "纯数字（页码形状）"),
    (r"(?i)\b(?:300750|CATL)\b", "已登记的案例形态"),
)


def _check_token_is_generic(typename: str, where: str, token: str) -> None:
    if not isinstance(token, str) or token == "":
        _err(typename, f"{where} 必须为非空字符串")
    for pattern, why in _FORBIDDEN_PROFILE_TOKEN_PATTERNS:
        if re.search(pattern, token):
            _err(typename, f"{where} 命中禁止字面形状（{why}）：{token!r}")


# ---------------------------------------------------------------------------
# 1. 标题归一化与模式匹配的叶子记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TitleNormalizationRule:
    """一条标题归一化规则（**公司无关**且可逐条复核）。"""

    order: int
    rule_key: str
    kind: str
    pattern: str | None
    replacement: str | None
    chars: str | None

    def __post_init__(self) -> None:
        t = "TitleNormalizationRule"
        _need_int({"v": self.order}, "v", t, lo=1)
        if not isinstance(self.rule_key, str) or self.rule_key == "":
            _err(t, "rule_key 必须为非空字符串")
        if self.kind not in NORMALIZATION_RULE_KINDS:
            _err(t, f"kind 必须属于 {NORMALIZATION_RULE_KINDS}，得到 {self.kind!r}")
        if self.kind == "regex_sub":
            if not isinstance(self.pattern, str) or self.pattern == "":
                _err(t, "regex_sub 必须给出 pattern")
            if not isinstance(self.replacement, str):
                _err(t, "regex_sub 必须给出 replacement（可为空串）")
            try:
                re.compile(self.pattern)
            except re.error as e:
                _err(t, f"pattern 不是合法正则：{e}")
            if self.chars is not None:
                _err(t, "regex_sub 不得携带 chars")
        elif self.kind == "strip_chars":
            if not isinstance(self.chars, str) or self.chars == "":
                _err(t, "strip_chars 必须给出非空 chars")
            if self.pattern is not None or self.replacement is not None:
                _err(t, "strip_chars 不得携带 pattern/replacement")
        else:
            if self.pattern is not None or self.replacement is not None \
                    or self.chars is not None:
                _err(t, "strip_whitespace 不得携带 pattern/replacement/chars")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TitleNormalizationRule",
            "order": self.order,
            "rule_key": self.rule_key,
            "kind": self.kind,
            "pattern": self.pattern,
            "replacement": self.replacement,
            "chars": self.chars,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TitleNormalizationRule":
        t = "TitleNormalizationRule"
        d = _reject_unknown(d, {
            "schema_type", "order", "rule_key", "kind", "pattern", "replacement",
            "chars"}, t)
        _need_enum(d, "schema_type", t, ("TitleNormalizationRule",))
        return cls(
            order=_need_int(d, "order", t, lo=1),
            rule_key=_need_str(d, "rule_key", t),
            kind=_need_enum(d, "kind", t, NORMALIZATION_RULE_KINDS),
            pattern=_need_str(d, "pattern", t, none_ok=True),
            replacement=_need_str(d, "replacement", t, none_ok=True,
                                  empty_ok=True),
            chars=_need_str(d, "chars", t, none_ok=True),
        )


@dataclass(frozen=True)
class SubjectStripRule:
    """**一次**主体前缀剥离：仅当直接匹配全部失败后才重试（`retry_once_after_no_match`）。

    它按通用公司形态后缀定位前缀，因此**不**编码任何公司名；`max_prefix_chars`
    限制被剥离的长度，"整句都是主体名"的异常标题因此不会被剥空。
    """

    kind: str
    suffixes: tuple[str, ...]
    max_prefix_chars: int
    apply_as: str

    def __post_init__(self) -> None:
        t = "SubjectStripRule"
        if self.kind != "corporate_suffix_prefix":
            _err(t, f"kind 只能为 'corporate_suffix_prefix'，得到 {self.kind!r}")
        if not isinstance(self.suffixes, tuple) or not self.suffixes:
            _err(t, "suffixes 必须为非空元组")
        for i, s in enumerate(self.suffixes):
            if not isinstance(s, str) or s == "":
                _err(t, f"suffixes[{i}] 必须为非空字符串")
        if len(set(self.suffixes)) != len(self.suffixes):
            _err(t, "suffixes 不得重复")
        # 真正的不变量是"长后缀必须先于会被它吞掉自己的短后缀"：否则"公司"先于
        # "股份有限公司"命中，会留下"股份有限"残渣。同长度之间的先后无关紧要，
        # 因此这里不做全序比较（那会把合法的资产误判为非法）。
        for i, short in enumerate(self.suffixes):
            for j in range(i + 1, len(self.suffixes)):
                longer = self.suffixes[j]
                if len(longer) > len(short) and longer.endswith(short):
                    _err(t, f"suffixes 顺序错误：{longer!r} 必须在 {short!r} 之前"
                            f"（否则 {short!r} 会先命中并留下残渣）")
        _need_int({"v": self.max_prefix_chars}, "v", t, lo=1)
        if self.apply_as != "retry_once_after_no_match":
            _err(t, "apply_as 只能为 'retry_once_after_no_match'")

    def to_dict(self) -> dict:
        return {
            "schema_type": "SubjectStripRule",
            "kind": self.kind,
            "suffixes": list(self.suffixes),
            "max_prefix_chars": self.max_prefix_chars,
            "apply_as": self.apply_as,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SubjectStripRule":
        t = "SubjectStripRule"
        d = _reject_unknown(d, {
            "schema_type", "kind", "suffixes", "max_prefix_chars", "apply_as"}, t)
        _need_enum(d, "schema_type", t, ("SubjectStripRule",))
        return cls(kind=_need_enum(d, "kind", t, ("corporate_suffix_prefix",)),
                   suffixes=_need_str_tuple(d, "suffixes", t),
                   max_prefix_chars=_need_int(d, "max_prefix_chars", t, lo=1),
                   apply_as=_need_enum(
                       d, "apply_as", t, ("retry_once_after_no_match",)))


@dataclass(frozen=True)
class HeadingPattern:
    """一条标题模式：`contains_any` / `contains_all` / `contains_all_with_any`。

    每条模式必须**自带**它要判定的 `structure_class`：这样"两条模式同时命中却给出
    不同结论"在分类器里可以被机械识别为冲突并 fail-closed 成 `unclassified`。
    """

    order: int
    pattern_key: str
    kind: str
    structure_class: str
    tokens: tuple[str, ...]
    all_tokens: tuple[str, ...]
    any_tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "HeadingPattern"
        _need_int({"v": self.order}, "v", t, lo=1)
        if not isinstance(self.pattern_key, str) or self.pattern_key == "":
            _err(t, "pattern_key 必须为非空字符串")
        if self.kind not in PATTERN_KINDS:
            _err(t, f"kind 必须属于 {PATTERN_KINDS}，得到 {self.kind!r}")
        if self.structure_class not in TABLES_STRUCTURE_CLASSES:
            _err(t, f"structure_class 必须属于 {TABLES_STRUCTURE_CLASSES}，"
                    f"得到 {self.structure_class!r}")
        if len(set(self.tokens)) != len(self.tokens):
            _err(t, "tokens 不得重复")
        if len(set(self.all_tokens)) != len(self.all_tokens):
            _err(t, "all_tokens 不得重复")
        if len(set(self.any_tokens)) != len(self.any_tokens):
            _err(t, "any_tokens 不得重复")
        if self.kind == "contains_any":
            if not self.tokens:
                _err(t, "contains_any 必须给出 tokens")
            if self.all_tokens or self.any_tokens:
                _err(t, "contains_any 不得携带 all_tokens/any_tokens")
            for i, tok in enumerate(self.tokens):
                _check_token_is_generic(t, f"tokens[{i}]", tok)
        elif self.kind == "contains_all":
            if not self.tokens:
                _err(t, "contains_all 必须给出 tokens")
            if self.all_tokens or self.any_tokens:
                _err(t, "contains_all 不得携带 all_tokens/any_tokens")
            for i, tok in enumerate(self.tokens):
                _check_token_is_generic(t, f"tokens[{i}]", tok)
        else:
            if self.tokens:
                _err(t, "contains_all_with_any 不得携带 tokens")
            if not self.all_tokens or not self.any_tokens:
                _err(t, "contains_all_with_any 必须同时给出 all_tokens 与 any_tokens")
            for i, tok in enumerate(self.all_tokens):
                _check_token_is_generic(t, f"all_tokens[{i}]", tok)
            for i, tok in enumerate(self.any_tokens):
                _check_token_is_generic(t, f"any_tokens[{i}]", tok)

    def matches(self, title: str) -> bool:
        if self.kind == "contains_any":
            return any(tok in title for tok in self.tokens)
        if self.kind == "contains_all":
            return all(tok in title for tok in self.tokens)
        return (all(tok in title for tok in self.all_tokens)
                and any(tok in title for tok in self.any_tokens))

    def to_dict(self) -> dict:
        return {
            "schema_type": "HeadingPattern",
            "order": self.order,
            "pattern_key": self.pattern_key,
            "kind": self.kind,
            "structure_class": self.structure_class,
            "tokens": list(self.tokens),
            "all_tokens": list(self.all_tokens),
            "any_tokens": list(self.any_tokens),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "HeadingPattern":
        t = "HeadingPattern"
        d = _reject_unknown(d, {
            "schema_type", "order", "pattern_key", "kind", "structure_class",
            "tokens", "all_tokens", "any_tokens"}, t)
        _need_enum(d, "schema_type", t, ("HeadingPattern",))
        return cls(
            order=_need_int(d, "order", t, lo=1),
            pattern_key=_need_str(d, "pattern_key", t),
            kind=_need_enum(d, "kind", t, PATTERN_KINDS),
            structure_class=_need_enum(d, "structure_class", t,
                                       TABLES_STRUCTURE_CLASSES),
            tokens=_need_str_tuple(d, "tokens", t),
            all_tokens=_need_str_tuple(d, "all_tokens", t),
            any_tokens=_need_str_tuple(d, "any_tokens", t),
        )


@dataclass(frozen=True)
class ClassificationPriorityRow:
    """§19.6.4 五条优先级行中的一条（顺序与 `when` 码都封闭）。"""

    order: int
    structure_class: str
    when: str

    def __post_init__(self) -> None:
        t = "ClassificationPriorityRow"
        _need_int({"v": self.order}, "v", t, lo=1, hi=len(
            CLASSIFICATION_PRIORITY_WHEN_CODES))
        expected = CLASSIFICATION_PRIORITY_WHEN_CODES[self.order - 1]
        if self.when != expected:
            _err(t, f"order={self.order} 的 when 必须为 {expected!r}，"
                    f"得到 {self.when!r}")
        if self.structure_class not in TABLES_STRUCTURE_CLASSES:
            _err(t, f"structure_class 必须属于 {TABLES_STRUCTURE_CLASSES}，"
                    f"得到 {self.structure_class!r}")

    def to_dict(self) -> dict:
        return {"schema_type": "ClassificationPriorityRow", "order": self.order,
                "structure_class": self.structure_class, "when": self.when}

    @classmethod
    def from_dict(cls, d: Any) -> "ClassificationPriorityRow":
        t = "ClassificationPriorityRow"
        d = _reject_unknown(d, {
            "schema_type", "order", "structure_class", "when"}, t)
        _need_enum(d, "schema_type", t, ("ClassificationPriorityRow",))
        return cls(order=_need_int(d, "order", t, lo=1),
                   structure_class=_need_enum(d, "structure_class", t,
                                              TABLES_STRUCTURE_CLASSES),
                   when=_need_str(d, "when", t))


# ---------------------------------------------------------------------------
# 2. 分类 profile（`tcp-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableClassificationProfile:
    """`tcp-1`：只读的 `structure_class` 规则资产。

    指纹覆盖**全部**规则内容（归一化规则、主体剥离、两类模式、优先级、冲突规则），
    因此改动任一模式/优先级/normalization version 必然改变 profile 指纹，进而改变
    `TableObjectV4` 的 `upstream_dependency_fingerprint`。
    """

    classification_profile_locator: str
    classification_profile_id: str
    schema_version: str
    profile_key: str
    profile_version: str
    normalization_version: str
    normalization_rules: tuple[TitleNormalizationRule, ...]
    subject_strip: SubjectStripRule
    note_subtree_patterns: tuple[HeadingPattern, ...]
    main_statement_patterns: tuple[HeadingPattern, ...]
    priority: tuple[ClassificationPriorityRow, ...]
    conflict_rule: str
    classification_profile_fingerprint: str

    SCHEMA_CONSTANT = "TABLE_CLASSIFICATION_PROFILE_VERSION"

    def __post_init__(self) -> None:
        t = "TableClassificationProfile"
        for name in ("classification_profile_locator", "classification_profile_id",
                     "profile_key", "profile_version", "normalization_version"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if self.profile_version != V.TABLE_CLASSIFICATION_PROFILE_VERSION:
            _err(t, f"profile_version 必须为 "
                    f"{V.TABLE_CLASSIFICATION_PROFILE_VERSION!r}，"
                    f"得到 {self.profile_version!r}")
        orders = [r.order for r in self.normalization_rules]
        if orders != list(range(1, len(orders) + 1)):
            _err(t, "normalization_rules 的 order 必须从 1 连续递增")
        keys = [r.rule_key for r in self.normalization_rules]
        if len(set(keys)) != len(keys):
            _err(t, "normalization_rules 的 rule_key 不得重复")
        if not isinstance(self.subject_strip, SubjectStripRule):
            _err(t, "subject_strip 必须为 SubjectStripRule")
        for name in ("note_subtree_patterns", "main_statement_patterns"):
            seq = getattr(self, name)
            if not isinstance(seq, tuple) or not seq:
                _err(t, f"{name} 必须为非空元组")
            porders = [p.order for p in seq]
            if porders != list(range(1, len(porders) + 1)):
                _err(t, f"{name} 的 order 必须从 1 连续递增")
            pkeys = [p.pattern_key for p in seq]
            if len(set(pkeys)) != len(pkeys):
                _err(t, f"{name} 的 pattern_key 不得重复")
            for i, p in enumerate(seq):
                if not isinstance(p, HeadingPattern):
                    _err(t, f"{name}[{i}] 必须为 HeadingPattern")
        for i, p in enumerate(self.note_subtree_patterns):
            if p.structure_class != "note_table":
                _err(t, f"note_subtree_patterns[{i}] 的 structure_class 必须为 "
                        f"'note_table'，得到 {p.structure_class!r}")
        for i, p in enumerate(self.main_statement_patterns):
            if p.structure_class != "financial_main_statement":
                _err(t, f"main_statement_patterns[{i}] 的 structure_class 必须为 "
                        f"'financial_main_statement'，得到 {p.structure_class!r}")
        if len(self.priority) != len(CLASSIFICATION_PRIORITY_WHEN_CODES):
            _err(t, f"priority 必须恰为 {len(CLASSIFICATION_PRIORITY_WHEN_CODES)} 行")
        for i, row in enumerate(self.priority):
            if not isinstance(row, ClassificationPriorityRow):
                _err(t, f"priority[{i}] 必须为 ClassificationPriorityRow")
            if row.order != i + 1:
                _err(t, f"priority[{i}].order 必须为 {i + 1}")
        if self.conflict_rule != "fail_closed_unclassified":
            _err(t, "conflict_rule 只能为 'fail_closed_unclassified'")
        expected_loc = locator("tcp", self.fingerprint_payload()["_locator_input"])
        if self.classification_profile_locator != expected_loc:
            _err(t, "classification_profile_locator 与派生定位不一致："
                    f"{self.classification_profile_locator!r} != {expected_loc!r}")
        _check_profile_fingerprint(t, "classification_profile_fingerprint",
                                   self.classification_profile_fingerprint,
                                   self.fingerprint_payload())
        expected_id = identity("tcp", self.identity_payload())
        if self.classification_profile_id != expected_id:
            _err(t, "classification_profile_id 与派生身份不一致："
                    f"{self.classification_profile_id!r} != {expected_id!r}")

    def fingerprint_payload(self) -> dict:
        payload = {
            "profile_key": self.profile_key,
            "schema_version": self.schema_version,
            "profile_version": self.profile_version,
            "normalization_version": self.normalization_version,
            "normalization_rules": [r.to_dict()
                                    for r in self.normalization_rules],
            "subject_strip": self.subject_strip.to_dict(),
            "note_subtree_patterns": [p.to_dict()
                                      for p in self.note_subtree_patterns],
            "main_statement_patterns": [p.to_dict()
                                        for p in self.main_statement_patterns],
            "priority": [row.to_dict() for row in self.priority],
            "conflict_rule": self.conflict_rule,
            "_locator_input": {"profile_key": self.profile_key,
                               "profile_version": self.profile_version},
        }
        return payload

    def locator_payload(self) -> dict:
        return {"profile_key": self.profile_key,
                "profile_version": self.profile_version}

    def identity_payload(self) -> dict:
        payload = {k: v for k, v in self.fingerprint_payload().items()
                   if k != "_locator_input"}
        payload["classification_profile_locator"] = \
            self.classification_profile_locator
        payload["classification_profile_fingerprint"] = \
            self.classification_profile_fingerprint
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "TableClassificationProfile"
        payload["classification_profile_id"] = self.classification_profile_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TableClassificationProfile":
        t = "TableClassificationProfile"
        d = _reject_unknown(d, {
            "schema_type", "classification_profile_locator",
            "classification_profile_id", "schema_version", "profile_key",
            "profile_version", "normalization_version", "normalization_rules",
            "subject_strip", "note_subtree_patterns", "main_statement_patterns",
            "priority", "conflict_rule",
            "classification_profile_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("TableClassificationProfile",))
        return cls(
            classification_profile_locator=_need_str(
                d, "classification_profile_locator", t),
            classification_profile_id=_need_str(
                d, "classification_profile_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            profile_key=_need_str(d, "profile_key", t),
            profile_version=_need_str(d, "profile_version", t),
            normalization_version=_need_str(d, "normalization_version", t),
            normalization_rules=_need_children(
                d, "normalization_rules", t, TitleNormalizationRule.from_dict),
            subject_strip=_need_child(d, "subject_strip", t,
                                      SubjectStripRule.from_dict),
            note_subtree_patterns=_need_children(
                d, "note_subtree_patterns", t, HeadingPattern.from_dict),
            main_statement_patterns=_need_children(
                d, "main_statement_patterns", t, HeadingPattern.from_dict),
            priority=_need_children(d, "priority", t,
                                    ClassificationPriorityRow.from_dict),
            conflict_rule=_need_str(d, "conflict_rule", t),
            classification_profile_fingerprint=_need_sha256(
                d, "classification_profile_fingerprint", t),
        )


# ---------------------------------------------------------------------------
# 3. cell block profile（`tcbp-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LineGroupRule:
    """行组定义：同一 cell 内**连续**的 `(页, 行)` 游程（允许不超过阈值的小间隙）。"""

    kind: str
    max_intra_group_line_gap: int

    def __post_init__(self) -> None:
        t = "LineGroupRule"
        if self.kind != "consecutive_page_line_runs":
            _err(t, f"kind 只能为 'consecutive_page_line_runs'，得到 {self.kind!r}")
        _need_int({"v": self.max_intra_group_line_gap}, "v", t, lo=0)

    def to_dict(self) -> dict:
        return {"schema_type": "LineGroupRule", "kind": self.kind,
                "max_intra_group_line_gap": self.max_intra_group_line_gap}

    @classmethod
    def from_dict(cls, d: Any) -> "LineGroupRule":
        t = "LineGroupRule"
        d = _reject_unknown(d, {
            "schema_type", "kind", "max_intra_group_line_gap"}, t)
        _need_enum(d, "schema_type", t, ("LineGroupRule",))
        return cls(kind=_need_enum(d, "kind", t, ("consecutive_page_line_runs",)),
                   max_intra_group_line_gap=_need_int(
                       d, "max_intra_group_line_gap", t, lo=0))


@dataclass(frozen=True)
class FontSignals:
    """字体信号的**相对**口径：基准是 cell 内中位行高，而不是任何绝对字号。"""

    body_size_reference: str
    heading_min_size_ratio: float
    size_tolerance: float

    def __post_init__(self) -> None:
        t = "FontSignals"
        if self.body_size_reference != "cell_block_median_line_height":
            _err(t, "body_size_reference 只能为 'cell_block_median_line_height'")
        _need_num({"v": self.heading_min_size_ratio}, "v", t, lo=1.0)
        _need_num({"v": self.size_tolerance}, "v", t, lo=0.0)

    def to_dict(self) -> dict:
        return {"schema_type": "FontSignals",
                "body_size_reference": self.body_size_reference,
                "heading_min_size_ratio": self.heading_min_size_ratio,
                "size_tolerance": self.size_tolerance}

    @classmethod
    def from_dict(cls, d: Any) -> "FontSignals":
        t = "FontSignals"
        d = _reject_unknown(d, {
            "schema_type", "body_size_reference", "heading_min_size_ratio",
            "size_tolerance"}, t)
        _need_enum(d, "schema_type", t, ("FontSignals",))
        return cls(
            body_size_reference=_need_enum(
                d, "body_size_reference", t, ("cell_block_median_line_height",)),
            heading_min_size_ratio=_need_num(d, "heading_min_size_ratio", t,
                                             lo=1.0),
            size_tolerance=_need_num(d, "size_tolerance", t, lo=0.0))


@dataclass(frozen=True)
class CellBlockRule:
    """一条 cell block 角色规则：`requires_all` 全部成立且 `requires_any` 至少一条成立。

    任何名字不在 `BLOCK_SIGNAL_NAMES` 内的信号都是**未登记信号**，构造期即拒绝：
    这样"规则引用了算不出来的证据"不会静默变成 False。
    """

    order: int
    rule_key: str
    role: str
    requires_all: tuple[str, ...]
    requires_any: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "CellBlockRule"
        _need_int({"v": self.order}, "v", t, lo=1)
        if self.rule_key not in CELL_BLOCK_RULE_KEYS:
            _err(t, f"rule_key 必须属于 {CELL_BLOCK_RULE_KEYS}，"
                    f"得到 {self.rule_key!r}")
        if self.role != self.rule_key:
            _err(t, f"role 必须等于 rule_key（得到 role={self.role!r}）")
        for name in ("requires_all", "requires_any"):
            seq = getattr(self, name)
            if not isinstance(seq, tuple):
                _err(t, f"{name} 必须为元组")
            if len(set(seq)) != len(seq):
                _err(t, f"{name} 不得重复")
            for i, sig in enumerate(seq):
                if sig not in BLOCK_SIGNAL_NAMES:
                    _err(t, f"{name}[{i}] 未登记信号名 {sig!r}"
                            f"（已登记 {BLOCK_SIGNAL_NAMES}）")
        if set(self.requires_all) & set(self.requires_any):
            _err(t, "requires_all 与 requires_any 不得交叉")
        if self.role == "unclassified" and (self.requires_all
                                            or self.requires_any):
            _err(t, "unclassified 是兜底规则，不得携带任何要求")

    def to_dict(self) -> dict:
        return {"schema_type": "CellBlockRule", "order": self.order,
                "rule_key": self.rule_key, "role": self.role,
                "requires_all": list(self.requires_all),
                "requires_any": list(self.requires_any)}

    @classmethod
    def from_dict(cls, d: Any) -> "CellBlockRule":
        t = "CellBlockRule"
        d = _reject_unknown(d, {
            "schema_type", "order", "rule_key", "role", "requires_all",
            "requires_any"}, t)
        _need_enum(d, "schema_type", t, ("CellBlockRule",))
        return cls(order=_need_int(d, "order", t, lo=1),
                   rule_key=_need_enum(d, "rule_key", t, CELL_BLOCK_RULE_KEYS),
                   role=_need_enum(d, "role", t, CELL_BLOCK_RULE_KEYS),
                   requires_all=_need_str_tuple(d, "requires_all", t),
                   requires_any=_need_str_tuple(d, "requires_any", t))


@dataclass(frozen=True)
class TableCellBlockProfile:
    """`tcbp-1`：cell 内角色规则资产（字体/编号/换行/相邻块，全部相对口径）。"""

    cell_block_profile_locator: str
    cell_block_profile_id: str
    schema_version: str
    profile_key: str
    profile_version: str
    normalization_version: str
    line_group_rule: LineGroupRule
    font_signals: FontSignals
    rules: tuple[CellBlockRule, ...]
    signals: dict
    conflict_rule: str
    unknown_signal_rule: str
    cell_block_profile_fingerprint: str

    SCHEMA_CONSTANT = "TABLE_CELL_BLOCK_PROFILE_VERSION"

    def __post_init__(self) -> None:
        t = "TableCellBlockProfile"
        for name in ("cell_block_profile_locator", "cell_block_profile_id",
                     "profile_key", "profile_version", "normalization_version"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if self.profile_version != V.TABLE_CELL_BLOCK_PROFILE_VERSION:
            _err(t, f"profile_version 必须为 "
                    f"{V.TABLE_CELL_BLOCK_PROFILE_VERSION!r}，"
                    f"得到 {self.profile_version!r}")
        if not isinstance(self.line_group_rule, LineGroupRule):
            _err(t, "line_group_rule 必须为 LineGroupRule")
        if not isinstance(self.font_signals, FontSignals):
            _err(t, "font_signals 必须为 FontSignals")
        if len(self.rules) != len(CELL_BLOCK_RULE_KEYS):
            _err(t, f"rules 必须恰为 {len(CELL_BLOCK_RULE_KEYS)} 条")
        seen: list[str] = []
        for i, r in enumerate(self.rules):
            if not isinstance(r, CellBlockRule):
                _err(t, f"rules[{i}] 必须为 CellBlockRule")
            if r.order != i + 1:
                _err(t, f"rules[{i}].order 必须为 {i + 1}")
            seen.append(r.role)
        if seen != list(CELL_BLOCK_RULE_KEYS):
            _err(t, f"rules 必须按固定顺序穷尽全部角色 {CELL_BLOCK_RULE_KEYS}，"
                    f"得到 {seen}")
        _check_signals_payload(t, self.signals)
        if self.conflict_rule != "fail_closed_unclassified":
            _err(t, "conflict_rule 只能为 'fail_closed_unclassified'")
        if self.unknown_signal_rule != "unclassified":
            _err(t, "unknown_signal_rule 只能为 'unclassified'")
        expected_loc = locator("tcbp", self.locator_payload())
        if self.cell_block_profile_locator != expected_loc:
            _err(t, "cell_block_profile_locator 与派生定位不一致："
                    f"{self.cell_block_profile_locator!r} != {expected_loc!r}")
        _check_profile_fingerprint(t, "cell_block_profile_fingerprint",
                                   self.cell_block_profile_fingerprint,
                                   self.fingerprint_payload())
        expected_id = identity("tcbp", self.identity_payload())
        if self.cell_block_profile_id != expected_id:
            _err(t, "cell_block_profile_id 与派生身份不一致："
                    f"{self.cell_block_profile_id!r} != {expected_id!r}")

    def fingerprint_payload(self) -> dict:
        return {
            "profile_key": self.profile_key,
            "schema_version": self.schema_version,
            "profile_version": self.profile_version,
            "normalization_version": self.normalization_version,
            "line_group_rule": self.line_group_rule.to_dict(),
            "font_signals": self.font_signals.to_dict(),
            "rules": [r.to_dict() for r in self.rules],
            "signals": self.signals,
            "conflict_rule": self.conflict_rule,
            "unknown_signal_rule": self.unknown_signal_rule,
        }

    def locator_payload(self) -> dict:
        return {"profile_key": self.profile_key,
                "profile_version": self.profile_version}

    def identity_payload(self) -> dict:
        payload = self.fingerprint_payload()
        payload["cell_block_profile_locator"] = self.cell_block_profile_locator
        payload["cell_block_profile_fingerprint"] = \
            self.cell_block_profile_fingerprint
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "TableCellBlockProfile"
        payload["cell_block_profile_id"] = self.cell_block_profile_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TableCellBlockProfile":
        t = "TableCellBlockProfile"
        d = _reject_unknown(d, {
            "schema_type", "cell_block_profile_locator", "cell_block_profile_id",
            "schema_version", "profile_key", "profile_version",
            "normalization_version", "line_group_rule", "font_signals", "rules",
            "signals", "conflict_rule", "unknown_signal_rule",
            "cell_block_profile_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("TableCellBlockProfile",))
        return cls(
            cell_block_profile_locator=_need_str(
                d, "cell_block_profile_locator", t),
            cell_block_profile_id=_need_str(d, "cell_block_profile_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            profile_key=_need_str(d, "profile_key", t),
            profile_version=_need_str(d, "profile_version", t),
            normalization_version=_need_str(d, "normalization_version", t),
            line_group_rule=_need_child(d, "line_group_rule", t,
                                        LineGroupRule.from_dict),
            font_signals=_need_child(d, "font_signals", t, FontSignals.from_dict),
            rules=_need_children(d, "rules", t, CellBlockRule.from_dict),
            signals=_need_mapping(d, "signals", t),
            conflict_rule=_need_str(d, "conflict_rule", t),
            unknown_signal_rule=_need_str(d, "unknown_signal_rule", t),
            cell_block_profile_fingerprint=_need_sha256(
                d, "cell_block_profile_fingerprint", t),
        )


#: `signals` 载荷的封闭字段集（多一个字段即失败，避免"悄悄引入新证据来源"）。
SIGNAL_PAYLOAD_FIELDS: tuple[str, ...] = (
    "list_markers", "sentence_terminators", "wrap_continuation_min_fill_ratio",
    "bold_span_majority_ratio",
)


def _check_signals_payload(typename: str, signals: Any) -> None:
    if not isinstance(signals, dict):
        _err(typename, "signals 必须为对象")
    extra = sorted(set(signals) - set(SIGNAL_PAYLOAD_FIELDS))
    missing = [k for k in SIGNAL_PAYLOAD_FIELDS if k not in signals]
    if extra:
        _err(typename, f"signals 含未登记字段：{extra}")
    if missing:
        _err(typename, f"signals 缺字段：{missing}")
    for name in ("list_markers", "sentence_terminators"):
        v = signals[name]
        if not isinstance(v, list) or not v:
            _err(typename, f"signals.{name} 必须为非空数组")
        for i, x in enumerate(v):
            _check_token_is_generic(typename, f"signals.{name}[{i}]", x)
        if len(set(v)) != len(v):
            _err(typename, f"signals.{name} 不得重复")
    for name in ("wrap_continuation_min_fill_ratio", "bold_span_majority_ratio"):
        _need_num({"v": signals[name]}, "v", typename, lo=0.0, hi=1.0)


def _need_mapping(d: dict, key: str, typename: str) -> dict:
    v = d.get(key)
    if not isinstance(v, dict):
        _err(typename, f"{key} 必须为对象，得到 {type(v).__name__}")
    return dict(v)


def _need_child(d: dict, key: str, typename: str, decoder: Callable) -> Any:
    v = d.get(key)
    if v is None:
        _err(typename, f"缺必填字段: {key}")
    try:
        return decoder(v)
    except SchemaValidationError as e:
        _err(typename, f"{key} 解码失败：{e}")


def _check_profile_fingerprint(typename: str, field: str, value: Any,
                               payload: dict) -> None:
    if not isinstance(value, str) or len(value) != 64:
        _err(typename, f"{field} 必须为 64 位小写十六进制 sha256，得到 {value!r}")
    expected = sha256_canonical(payload)
    if value != expected:
        _err(typename, f"{field} 与载荷重算不一致：{value!r} != {expected!r}")


# ---------------------------------------------------------------------------
# 4. 资产装载（fail-closed）
# ---------------------------------------------------------------------------

def profiles_directory() -> str:
    """只读资产目录（与 `document_structure` 包同级的 `policies/`）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        PROFILES_DIR_NAME)


@dataclass(frozen=True)
class TableProfileBundle:
    """两份 profile 及其**文件身份**的绑定结果（不是 wire 类型，只用于运行时）。"""

    classification: TableClassificationProfile
    cell_block: TableCellBlockProfile
    classification_file_sha256: str
    cell_block_file_sha256: str
    registry_version: str
    registry_file_sha256: str
    profiles_dir: str

    def classification_profile_version(self) -> str:
        return self.classification.profile_version

    def cell_block_profile_version(self) -> str:
        return self.cell_block.profile_version

    def classification_file_fingerprint(self) -> str:
        return self.classification_file_sha256

    def classification_content_fingerprint(self) -> str:
        return self.classification.classification_profile_fingerprint

    def cell_block_file_fingerprint(self) -> str:
        return self.cell_block_file_sha256

    def cell_block_content_fingerprint(self) -> str:
        return self.cell_block.cell_block_profile_fingerprint

    def settings_fingerprint(self) -> str:
        """两份 profile 的**种类无关**设置指纹（进入上游依赖束）。"""
        return sha256_canonical({
            "registry_version": self.registry_version,
            "registry_file_sha256": self.registry_file_sha256,
            "classification": {
                "profile_key": self.classification.profile_key,
                "profile_version": self.classification.profile_version,
                "file_sha256": self.classification_file_sha256,
                "content_fingerprint": self.classification_content_fingerprint(),
            },
            "cell_block": {
                "profile_key": self.cell_block.profile_key,
                "profile_version": self.cell_block.profile_version,
                "file_sha256": self.cell_block_file_sha256,
                "content_fingerprint": self.cell_block_content_fingerprint(),
            },
        })


def _load_json_object(path: str, typename: str) -> dict:
    if not os.path.isfile(path):
        _err(typename, f"只读资产不存在：{path}")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        d = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        _err(typename, f"资产不是合法 UTF-8：{path}（{e}）")
    except json.JSONDecodeError as e:
        _err(typename, f"资产不是合法 JSON：{path}（{e}）")
    if not isinstance(d, dict):
        _err(typename, f"资产顶层必须为对象：{path}")
    return d


def _file_sha256(path: str) -> str:
    # 缺失/不可读必须是**可判定的 fail-closed**，不能把 IOError 漏成调用方崩溃。
    if not os.path.isfile(path):
        _err("TableProfileRegistry", f"只读资产不存在：{path}")
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError as e:
        _err("TableProfileRegistry", f"只读资产不可读：{path}（{e}）")


def load_table_profile_bundle(profiles_dir: str | None = None) -> \
        TableProfileBundle:
    """读入并校验两份 profile + 注册表；任何漂移都 fail-closed。

    校验链（缺一不可）：

    1. 注册表存在、`schema_type` 与 `registry_version` 精确匹配；
    2. 每个登记项有 `file` / `file_sha256` / `profile_fingerprint` / `profile_kind`，
       且 `default_profile_key` 指向一个登记的 classification profile；
    3. 文件 **raw bytes SHA256** 与登记值一致（字节漂移即拒绝）；
    4. 载入对象的 **canonical 内容指纹** 与登记值一致（模式漂移即拒绝）；
    5. 两类各恰一份，且种类与 `profile_kind` 一致。
    """
    base = profiles_dir or profiles_directory()
    registry_path = os.path.join(base, PROFILE_REGISTRY_FILE)
    reg = _load_json_object(registry_path, "TableProfileRegistry")
    reg = _reject_unknown(reg, {
        "schema_type", "registry_version", "default_profile_key",
        "profiles"}, "TableProfileRegistry")
    if reg.get("schema_type") != "TableProfileRegistry":
        _err("TableProfileRegistry",
             f"schema_type 必须为 'TableProfileRegistry'，得到 "
             f"{reg.get('schema_type')!r}")
    if reg.get("registry_version") != TABLE_PROFILE_REGISTRY_VERSION:
        _err("TableProfileRegistry",
             f"registry_version 必须为 {TABLE_PROFILE_REGISTRY_VERSION!r}，"
             f"得到 {reg.get('registry_version')!r}")
    profiles = reg.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        _err("TableProfileRegistry", "profiles 必须为非空对象")
    by_kind: dict[str, tuple[str, dict]] = {}
    for key, entry in sorted(profiles.items()):
        if not isinstance(entry, dict):
            _err("TableProfileRegistry", f"profiles[{key!r}] 必须为对象")
        entry = _reject_unknown(entry, {
            "file", "file_sha256", "profile_fingerprint", "profile_kind"},
            "TableProfileRegistry")
        kind = entry.get("profile_kind")
        if kind not in PROFILE_KINDS:
            _err("TableProfileRegistry",
                 f"profiles[{key!r}].profile_kind 必须属于 {PROFILE_KINDS}，"
                 f"得到 {kind!r}")
        if kind in by_kind:
            _err("TableProfileRegistry",
                 f"profile_kind={kind!r} 被登记了多次（{by_kind[kind][0]!r} / "
                 f"{key!r}）")
        fname = entry.get("file")
        if not isinstance(fname, str) or fname == "" or \
                os.path.basename(fname) != fname:
            _err("TableProfileRegistry",
                 f"profiles[{key!r}].file 必须为目录内文件名，得到 {fname!r}")
        for name in ("file_sha256", "profile_fingerprint"):
            v = entry.get(name)
            if not isinstance(v, str) or len(v) != 64:
                _err("TableProfileRegistry",
                     f"profiles[{key!r}].{name} 必须为 sha256，得到 {v!r}")
        by_kind[kind] = (key, dict(entry))
    missing_kinds = sorted(set(PROFILE_KINDS) - set(by_kind))
    if missing_kinds:
        _err("TableProfileRegistry",
             f"注册表未登记这些 profile 种类：{missing_kinds}")
    default_key = reg.get("default_profile_key")
    if default_key != by_kind["classification"][0]:
        _err("TableProfileRegistry",
             f"default_profile_key 必须指向登记的 classification profile "
             f"{by_kind['classification'][0]!r}，得到 {default_key!r}")

    loaded: dict[str, Any] = {}
    file_shas: dict[str, str] = {}
    for kind in PROFILE_KINDS:
        key, entry = by_kind[kind]
        path = os.path.join(base, entry["file"])
        actual_file_sha = _file_sha256(path)
        if actual_file_sha != entry["file_sha256"]:
            _err("TableProfileRegistry",
                 f"profile {key!r} 的文件字节漂移："
                 f"{actual_file_sha} != {entry['file_sha256']}")
        raw = _load_json_object(path, key)
        obj = (TableClassificationProfile.from_dict(raw) if kind == "classification"
               else TableCellBlockProfile.from_dict(raw))
        if obj.profile_key != key:
            _err("TableProfileRegistry",
                 f"profile {key!r} 的文件内 profile_key 为 {obj.profile_key!r}")
        content_fp = (obj.classification_profile_fingerprint
                      if kind == "classification"
                      else obj.cell_block_profile_fingerprint)
        if content_fp != entry["profile_fingerprint"]:
            _err("TableProfileRegistry",
                 f"profile {key!r} 的内容指纹漂移："
                 f"{content_fp} != {entry['profile_fingerprint']}")
        loaded[kind] = obj
        file_shas[kind] = actual_file_sha

    return TableProfileBundle(
        classification=loaded["classification"],
        cell_block=loaded["cell_block"],
        classification_file_sha256=file_shas["classification"],
        cell_block_file_sha256=file_shas["cell_block"],
        registry_version=TABLE_PROFILE_REGISTRY_VERSION,
        registry_file_sha256=_file_sha256(registry_path),
        profiles_dir=os.path.abspath(base),
    )


def load_classification_profile(profiles_dir: str | None = None) -> \
        TableClassificationProfile:
    return load_table_profile_bundle(profiles_dir).classification


def load_cell_block_profile(profiles_dir: str | None = None) -> \
        TableCellBlockProfile:
    return load_table_profile_bundle(profiles_dir).cell_block


# ---------------------------------------------------------------------------
# 5. 标题归一化
# ---------------------------------------------------------------------------

def _apply_normalization_rules(profile: TableClassificationProfile,
                              title: str) -> str:
    out = title
    for rule in profile.normalization_rules:
        if rule.kind == "strip_whitespace":
            out = re.sub(r"\s+", "", out)
        elif rule.kind == "regex_sub":
            out = re.sub(rule.pattern, rule.replacement or "", out)
        else:
            out = out.strip(rule.chars or "")
    return out


def _apply_subject_strip(profile: TableClassificationProfile,
                         title: str) -> str:
    """一次主体前缀剥离：通用公司形态后缀定位前缀，剥离长度受限。

    只剥**最长**匹配后缀之前的全部前缀；若无后缀或前缀长度超限则原样返回。
    """
    rule = profile.subject_strip
    best: tuple[int, int] | None = None
    for suffix in rule.suffixes:
        idx = title.find(suffix)
        if idx < 0:
            continue
        prefix_len = idx + len(suffix)
        if prefix_len > rule.max_prefix_chars:
            continue
        rest = title[prefix_len:]
        if rest == "":
            continue
        candidate = (prefix_len, -len(suffix))
        if best is None or candidate > best:
            best = candidate
    if best is None:
        return title
    return title[best[0]:]


def normalize_heading_title(profile: TableClassificationProfile,
                            title: str) -> str:
    """返回**主**归一化形式（未做主体剥离）。"""
    if not isinstance(title, str):
        _err("normalize_heading_title", "title 必须为字符串")
    return _apply_normalization_rules(profile, title)


def heading_match_candidates(profile: TableClassificationProfile,
                             title: str) -> tuple[str, ...]:
    """返回**至多两个**候选：主形式，以及"主体剥离后重试"的形式。

    顺序固定，因此匹配结果确定；`apply_as="retry_once_after_no_match"` 的语义由
    调用方保证——只有主形式一个模式都不命中时才看第二个候选。
    """
    primary = _apply_normalization_rules(profile, title)
    stripped = _apply_subject_strip(profile, primary)
    if stripped == primary:
        return (primary,)
    return (primary, stripped)


# ---------------------------------------------------------------------------
# 6. `structure_class` 裁决（§19.6.4 五行真值表）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableClassificationInputs:
    """分类器**全部**允许输入（§19.6.4）。

    它**不含** synopsis、不含任何 Evidence 文本、不含页码或表号。
    `ancestor_titles` 是从根到 owner node 的**已验证**真实标题路径（含 owner 自身）。
    """

    owner_kind: str
    owner_resolved: bool
    ancestor_titles: tuple[str, ...]
    inside_verified_body: bool
    path_available: bool

    def __post_init__(self) -> None:
        t = "TableClassificationInputs"
        if self.owner_kind not in ("outline_node", "unassigned_boundary"):
            _err(t, f"owner_kind 必须为 outline_node/unassigned_boundary，"
                    f"得到 {self.owner_kind!r}")
        if not isinstance(self.ancestor_titles, tuple):
            _err(t, "ancestor_titles 必须为元组")
        for i, x in enumerate(self.ancestor_titles):
            if not isinstance(x, str) or x == "":
                _err(t, f"ancestor_titles[{i}] 必须为非空字符串")
        for name in ("owner_resolved", "inside_verified_body", "path_available"):
            if not isinstance(getattr(self, name), bool):
                _err(t, f"{name} 必须为 bool")
        # boundary owner 没有标题路径；带路径的 boundary 是自相矛盾的输入。
        if self.owner_kind == "unassigned_boundary":
            if self.ancestor_titles:
                _err(t, "unassigned_boundary owner 不得携带标题路径")
            if self.path_available:
                _err(t, "unassigned_boundary owner 不得声明 path_available")

    def path_usable(self) -> bool:
        return (self.owner_kind == "outline_node" and self.path_available
                and len(self.ancestor_titles) > 0)


def _matching_classes(patterns: Sequence[HeadingPattern],
                      candidates: Sequence[str]) -> tuple[tuple[str, ...], bool]:
    """返回 `(命中的 structure_class 集合, 是否至少有一个候选产生命中)`。

    候选按序尝试：**第一个**产生命中的候选决定匹配集合（保证确定性），
    之后的候选不再参与。
    """
    for candidate in candidates:
        hits = [p for p in patterns if p.matches(candidate)]
        if hits:
            return tuple(sorted({p.structure_class for p in hits})), True
    return (), False


def classify_structure_class(profile: TableClassificationProfile,
                             inputs: TableClassificationInputs) -> \
        tuple[str, str]:
    """按 §19.6.4 的五行优先级裁决 `structure_class`，返回 `(class, 裁决码)`。

    任一行内"多条模式同时命中且给出不同 `structure_class`"⇒ `unclassified`
    （fail-closed），不靠"取第一条"掩盖歧义。
    """
    if not isinstance(profile, TableClassificationProfile):
        _err("classify_structure_class", "profile 必须为 TableClassificationProfile")
    if not isinstance(inputs, TableClassificationInputs):
        _err("classify_structure_class", "inputs 必须为 TableClassificationInputs")
    # 行 1：owner / path / boundary **不可用或无法唯一解释**。
    # 注意：已验证但未归属的 boundary 不是"不可用"，它由行 4 接住。
    if not inputs.owner_resolved:
        return "unclassified", "owner_path_unavailable"
    if inputs.owner_kind == "outline_node" and not inputs.path_usable():
        return "unclassified", "owner_path_unavailable"
    # 行 2：已验证祖先路径命中附注模式（只有 outline_node owner 才有路径）。
    if inputs.path_usable():
        for title in inputs.ancestor_titles:
            cands = heading_match_candidates(profile, title)
            classes, hit = _matching_classes(profile.note_subtree_patterns, cands)
            if hit:
                if len(classes) != 1:
                    return "unclassified", "pattern_conflict"
                return "note_table", "ancestor_path_note_pattern"
        # 行 3：不在附注子树内，且最近有效标题命中主表模式。
        nearest = heading_match_candidates(profile, inputs.ancestor_titles[-1])
        classes, hit = _matching_classes(profile.main_statement_patterns, nearest)
        if hit:
            if len(classes) != 1:
                return "unclassified", "pattern_conflict"
            return ("financial_main_statement",
                    "nearest_heading_main_statement_pattern")
    # 行 4：位于已验证正文节点/边界内。
    if inputs.inside_verified_body:
        return "ordinary_business_table", "inside_verified_body"
    # 行 5：其余。
    return "unclassified", "no_rule_matched"


# ---------------------------------------------------------------------------
# 7. cell block 角色裁决（§19.6.2）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CellBlockSignals:
    """一个 cell 内单个候选块的**全部**可用信号（未取到的用 `None`）。"""

    text: str
    line_group_count: int
    own_line_group: bool
    followed_by_other_block_in_same_cell: bool
    unclosed_inputs: bool
    numbering_marker_prefix: bool
    starts_with_list_marker: bool
    has_sentence_terminator: bool
    wrap_continuation: bool
    size_ratio: float | None
    bold_ratio: float | None

    def __post_init__(self) -> None:
        t = "CellBlockSignals"
        if not isinstance(self.text, str):
            _err(t, "text 必须为字符串")
        _need_int({"v": self.line_group_count}, "v", t, lo=0)
        for name in ("own_line_group", "followed_by_other_block_in_same_cell",
                     "unclosed_inputs", "numbering_marker_prefix",
                     "starts_with_list_marker", "has_sentence_terminator",
                     "wrap_continuation"):
            if not isinstance(getattr(self, name), bool):
                _err(t, f"{name} 必须为 bool")
        for name in ("size_ratio", "bold_ratio"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, (int, float))
                                  or isinstance(v, bool)):
                _err(t, f"{name} 必须为实数或 None，得到 {v!r}")

    def value(self, name: str) -> bool | None:
        """按**已登记**信号名求值；未登记名一律 `None`（⇒ 规则视为不成立）。"""
        if name == "own_line_group":
            return self.own_line_group
        if name == "single_line_group":
            return self.line_group_count == 1
        if name == "line_group_count_at_least_2":
            return self.line_group_count >= 2
        if name == "followed_by_other_block_in_same_cell":
            return self.followed_by_other_block_in_same_cell
        if name == "unclosed_inputs":
            return self.unclosed_inputs
        if name == "numbering_marker_prefix":
            return self.numbering_marker_prefix
        if name == "starts_with_list_marker":
            return self.starts_with_list_marker
        if name == "has_sentence_terminator":
            return self.has_sentence_terminator
        if name == "no_sentence_terminator":
            return not self.has_sentence_terminator
        if name == "wrap_continuation":
            return self.wrap_continuation
        if name == "size_ratio_at_least_heading_min":
            return None  # 需要 profile 阈值，由 classify 注入
        if name == "bold_span_majority":
            return None  # 需要 profile 阈值，由 classify 注入
        return None

    def to_dict(self) -> dict:
        return {
            "schema_type": "CellBlockSignals",
            "text": self.text,
            "line_group_count": self.line_group_count,
            "own_line_group": self.own_line_group,
            "followed_by_other_block_in_same_cell":
                self.followed_by_other_block_in_same_cell,
            "unclosed_inputs": self.unclosed_inputs,
            "numbering_marker_prefix": self.numbering_marker_prefix,
            "starts_with_list_marker": self.starts_with_list_marker,
            "has_sentence_terminator": self.has_sentence_terminator,
            "wrap_continuation": self.wrap_continuation,
            "size_ratio": self.size_ratio,
            "bold_ratio": self.bold_ratio,
        }


def evaluate_cell_block_signals(*, text: str, line_group_count: int,
                                own_line_group: bool,
                                followed_by_other_block_in_same_cell: bool,
                                size_ratio: float | None,
                                bold_ratio: float | None,
                                unclosed_inputs: bool,
                                profile: TableCellBlockProfile) -> CellBlockSignals:
    """由**真实块输入**求值全部信号；profile 只提供"什么算列表标记/句末标点"。"""
    markers = tuple(profile.signals["list_markers"])
    terminators = tuple(profile.signals["sentence_terminators"])
    stripped = text.lstrip()
    return CellBlockSignals(
        text=text,
        line_group_count=line_group_count,
        own_line_group=own_line_group,
        followed_by_other_block_in_same_cell=followed_by_other_block_in_same_cell,
        unclosed_inputs=unclosed_inputs,
        numbering_marker_prefix=_has_numbering_prefix(stripped),
        starts_with_list_marker=any(stripped.startswith(m) for m in markers),
        has_sentence_terminator=any(stripped.endswith(t) or t in stripped
                                    for t in terminators),
        wrap_continuation=False,
        size_ratio=size_ratio,
        bold_ratio=bold_ratio,
    )


def _has_numbering_prefix(text: str) -> bool:
    """通用编号前缀：`第N…`、`（N）`/`(N)`、`N.`/`N、` —— 与公司无关。"""
    return bool(re.match(
        r"^(?:第[一二三四五六七八九十百0-9]+[条节款项目]"
        r"|[（(][一二三四五六七八九十百0-9]+[)）]"
        r"|[0-9]+[、.．)）])", text))


def _resolve_signal(profile: TableCellBlockProfile, signals: CellBlockSignals,
                    name: str) -> bool:
    """把一个信号名解成 bool；阈值类信号在这里注入 profile 阈值。"""
    if name == "size_ratio_at_least_heading_min":
        if signals.size_ratio is None:
            return False
        return signals.size_ratio >= \
            (profile.font_signals.heading_min_size_ratio
             - profile.font_signals.size_tolerance)
    if name == "bold_span_majority":
        if signals.bold_ratio is None:
            return False
        return signals.bold_ratio >= \
            (profile.signals["bold_span_majority_ratio"]
             - profile.font_signals.size_tolerance)
    return bool(signals.value(name))


def classify_cell_block_role(profile: TableCellBlockProfile,
                             signals: CellBlockSignals) -> tuple[str, str]:
    """按 §19.6.2 的角色真值表裁决 block role，返回 `(role, 裁决码)`。

    - 输入不闭合 ⇒ `unclassified`（不得靠猜角色提高命中率）；
    - 同一 `order` 上多条规则同时成立 ⇒ `unclassified`（fail-closed）；
    - 无任何规则成立时由兜底 `unclassified` 规则接住。
    """
    if not isinstance(profile, TableCellBlockProfile):
        _err("classify_cell_block_role", "profile 必须为 TableCellBlockProfile")
    if not isinstance(signals, CellBlockSignals):
        _err("classify_cell_block_role", "signals 必须为 CellBlockSignals")
    if signals.unclosed_inputs:
        return "unclassified", "unclosed_inputs"
    matches: list[CellBlockRule] = []
    for rule in profile.rules:
        if not all(_resolve_signal(profile, signals, s)
                   for s in rule.requires_all):
            continue
        if rule.requires_any and not any(_resolve_signal(profile, signals, s)
                                         for s in rule.requires_any):
            continue
        matches.append(rule)
    if not matches:
        return "unclassified", "rule_unclassified"
    orders = {r.order for r in matches}
    if len(orders) != 1:
        # 首个 order 生效（profile 顺序即优先级），但同 order 冲突必须 fail-closed。
        first = min(r.order for r in matches)
        at_first = [r for r in matches if r.order == first]
        if len(at_first) > 1:
            return "unclassified", "rule_conflict"
        return at_first[0].role, f"rule_{at_first[0].role}"
    if len(matches) > 1:
        return "unclassified", "rule_conflict"
    return matches[0].role, f"rule_{matches[0].role}"


# ---------------------------------------------------------------------------
# 8. 自检
# ---------------------------------------------------------------------------

def _profile_generic_token_problems(profile: TableClassificationProfile) -> \
        list[str]:
    problems: list[str] = []
    tokens: list[tuple[str, str]] = []
    for name, patterns in (("note_subtree_patterns", profile.note_subtree_patterns),
                           ("main_statement_patterns",
                            profile.main_statement_patterns)):
        for p in patterns:
            for i, tok in enumerate(p.tokens):
                tokens.append((f"{name}[{p.pattern_key}].tokens[{i}]", tok))
            for i, tok in enumerate(p.all_tokens):
                tokens.append((f"{name}[{p.pattern_key}].all_tokens[{i}]", tok))
            for i, tok in enumerate(p.any_tokens):
                tokens.append((f"{name}[{p.pattern_key}].any_tokens[{i}]", tok))
    for where, tok in tokens:
        try:
            _check_token_is_generic("TableClassificationProfile", where, tok)
        except SchemaValidationError as e:
            problems.append(str(e))
    return problems


def self_check(profiles_dir: str | None = None) -> dict:
    """装载 + 真值表 + 反硬编码的机械自检。"""
    problems: list[str] = []
    bundle = None
    try:
        bundle = load_table_profile_bundle(profiles_dir)
    except SchemaValidationError as e:
        problems.append(f"profile 装载失败：{e}")
    if bundle is not None:
        problems.extend(_profile_generic_token_problems(bundle.classification))
        if bundle.registry_version != TABLE_PROFILE_REGISTRY_VERSION:
            problems.append("registry_version 与模块常量不一致")
        # 每个角色都必须能被**至少一条**规则产出（兜底规则保证 unclassified）。
        produced = {r.role for r in bundle.cell_block.rules}
        if produced != set(CELL_BLOCK_RULE_KEYS):
            problems.append(f"cell block 规则未穷尽角色：{sorted(produced)}")
        # 分类真值表：五条行都必须**可达**。行 2/行 3 的探针标题由 profile 自身的
        # 模式 token 现场合成，因此自检里不出现任何公司、代码或样本答案。
        cprofile = bundle.classification
        note_title = cprofile.note_subtree_patterns[0].tokens[0]
        main_title = cprofile.main_statement_patterns[0].tokens[0]
        probes = (
            ("row1_owner_unresolved",
             {"owner_kind": "unassigned_boundary", "owner_resolved": False,
              "ancestor_titles": (), "inside_verified_body": True,
              "path_available": False},
             "unclassified", "owner_path_unavailable"),
            ("row1_node_without_path",
             {"owner_kind": "outline_node", "owner_resolved": True,
              "ancestor_titles": (), "inside_verified_body": True,
              "path_available": False},
             "unclassified", "owner_path_unavailable"),
            ("row2_note_ancestor",
             {"owner_kind": "outline_node", "owner_resolved": True,
              "ancestor_titles": (note_title,), "inside_verified_body": True,
              "path_available": True},
             "note_table", "ancestor_path_note_pattern"),
            ("row3_nearest_main_statement",
             {"owner_kind": "outline_node", "owner_resolved": True,
              "ancestor_titles": ("其他", main_title),
              "inside_verified_body": True, "path_available": True},
             "financial_main_statement",
             "nearest_heading_main_statement_pattern"),
            ("row4_inside_verified_body",
             {"owner_kind": "outline_node", "owner_resolved": True,
              "ancestor_titles": ("其他",), "inside_verified_body": True,
              "path_available": True},
             "ordinary_business_table", "inside_verified_body"),
            ("row4_resolved_boundary",
             {"owner_kind": "unassigned_boundary", "owner_resolved": True,
              "ancestor_titles": (), "inside_verified_body": True,
              "path_available": False},
             "ordinary_business_table", "inside_verified_body"),
            ("row5_otherwise",
             {"owner_kind": "outline_node", "owner_resolved": True,
              "ancestor_titles": ("其他",), "inside_verified_body": False,
              "path_available": True},
             "unclassified", "no_rule_matched"),
        )
        for label, kwargs, want_class, want_code in probes:
            got_class, got_code = classify_structure_class(
                cprofile, TableClassificationInputs(**kwargs))
            if (got_class, got_code) != (want_class, want_code):
                problems.append(
                    f"分类真值表探针 {label} 失败：得到 "
                    f"{(got_class, got_code)}，期望 {(want_class, want_code)}")
        # cell block 真值表：五个角色必须都可被至少一条规则产出。
        bprofile = bundle.cell_block
        if bprofile.conflict_rule != "fail_closed_unclassified":
            problems.append("cell block conflict_rule 不是 fail-closed")
        if bprofile.unknown_signal_rule != "unclassified":
            problems.append("cell block unknown_signal_rule 不是 unclassified")
        if cprofile.conflict_rule != "fail_closed_unclassified":
            problems.append("classification conflict_rule 不是 fail-closed")
    return {
        "registry_version": TABLE_PROFILE_REGISTRY_VERSION,
        "profile_kind_count": len(PROFILE_KINDS),
        "classification_pattern_count": (
            len(bundle.classification.note_subtree_patterns)
            + len(bundle.classification.main_statement_patterns)
            if bundle else 0),
        "cell_block_rule_count": (len(bundle.cell_block.rules) if bundle else 0),
        "block_signal_name_count": len(BLOCK_SIGNAL_NAMES),
        "classification_profile_version": (
            bundle.classification_profile_version() if bundle else None),
        "cell_block_profile_version": (
            bundle.cell_block_profile_version() if bundle else None),
        "classification_file_sha256": (
            bundle.classification_file_sha256 if bundle else None),
        "cell_block_file_sha256": (
            bundle.cell_block_file_sha256 if bundle else None),
        "settings_fingerprint": (
            bundle.settings_fingerprint() if bundle else None),
        "problems": problems,
    }


def _main(argv: list[str] | None = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--self-check",):
        print(f"未知参数: {args[0]}", file=sys.stderr)
        return 2
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report["problems"] else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))
