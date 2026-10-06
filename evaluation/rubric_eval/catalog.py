# -*- coding: utf-8 -*-
"""评测指标目录（静态）：24 个指标的定义、四维主归属、执行环节、分子分母与判断方式。

这份目录是 `EVALUATION_RUBRIC_WEBSITE_FORM.md` 与 `EVALUATION_RUBRIC_V2_DISCUSSION.md`
的**机器可读副本**，只描述"我们怎样评"，不含任何本次 run 的分数。它与每次运行的结果
分开存放，因此"改了措辞"与"改了某个 run 测出什么"不会互相污染。

两个受控词表：

* :data:`DIMENSIONS` —— 四个评测维度（网站矩阵的四行）。
* :data:`STAGES` —— 八个执行环节（网站矩阵的八列）。
* :data:`DECIDERS` —— 定这条读数的是谁：`AUTO`（程序可确定性复算）、`GOLD`（需离线标注集）、
  `HUMAN`（需人工评阅）、`MIXED`（机械部分可算、业务部分需 Gold/人读）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

CATALOG_VERSION = "rbeval-catalog-1"

#: 四个评测维度，顺序即网站矩阵的行序。
DIMENSIONS: tuple[str, ...] = (
    "执行正确性", "传递保真度", "业务质量", "可观测性",
)

#: 八个执行环节，顺序即网站矩阵的列序。
STAGES: tuple[str, ...] = (
    "开发与版本", "上传与财务权威", "文档结构与树图", "Router 与混合检索",
    "Harness 与 Topic Pack", "写作与财务呈现", "独立审阅与放行", "展示与人工接受",
)

#: 判定来源。
DECIDERS: tuple[str, ...] = ("AUTO", "GOLD", "HUMAN", "MIXED")

#: 测量状态受控词表。`MEASURED` 才允许带数值。
MEASUREMENT_STATES: tuple[str, ...] = (
    "MEASURED", "BENCHMARK_PENDING", "HUMAN_PENDING", "NOT_RUN_UPSTREAM",
    "NOT_IN_SCOPE", "NOT_IMPLEMENTED", "FAILED_TO_MEASURE",
)

#: 执行状态受控词表。它回答"这一环跑没跑"，与"测出了什么"分开。
EXECUTION_STATES: tuple[str, ...] = (
    "DID_RUN", "PARTIAL", "NOT_RUN_UPSTREAM", "FAILED",
)


@dataclass(frozen=True)
class MetricSpec:
    """一个指标的定义。字段全部来自评测文档，不来自某次运行。"""

    metric_id: str
    title: str
    primary_dimension: str
    dimensions: tuple[str, ...]
    stage: str
    definition: str
    numerator_spec: str
    denominator_spec: str
    method: str
    decided_by: str
    scope: str
    required_evidence: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "title": self.title,
            "primary_dimension": self.primary_dimension,
            "dimensions": list(self.dimensions),
            "stage": self.stage,
            "definition": self.definition,
            "numerator_spec": self.numerator_spec,
            "denominator_spec": self.denominator_spec,
            "method": self.method,
            "decided_by": self.decided_by,
            "scope": self.scope,
            "required_evidence": list(self.required_evidence),
            "limitations": list(self.limitations),
        }


def _m(*, metric_id: str, title: str, primary_dimension: str, dimensions: tuple[str, ...],
       stage: str, definition: str, numerator_spec: str, denominator_spec: str,
       method: str, decided_by: str, scope: str,
       required_evidence: tuple[str, ...] = (), limitations: tuple[str, ...] = ()
       ) -> MetricSpec:
    return MetricSpec(
        metric_id=metric_id, title=title, primary_dimension=primary_dimension,
        dimensions=dimensions, stage=stage, definition=definition,
        numerator_spec=numerator_spec, denominator_spec=denominator_spec, method=method,
        decided_by=decided_by, scope=scope, required_evidence=required_evidence,
        limitations=limitations)


METRICS: tuple[MetricSpec, ...] = (
    # ---- 开发与版本 -------------------------------------------------------
    _m(metric_id="D01", title="回归执行",
       primary_dimension="执行正确性", dimensions=("执行正确性",), stage="开发与版本",
       definition="本次改动直接受影响的测试批次的实际运行结果，分别报通过、失败、跳过；全量测试是否本批运行单列。",
       numerator_spec="通过的受影响测试数",
       denominator_spec="本次实际运行的受影响测试数",
       method="以测试命令、退出码与完整输出为准；上批结果不得冒充本批，通过数也不代表报告业务合格。",
       decided_by="AUTO", scope="开发批次（与报告 run 分账）",
       required_evidence=("评测脚本自带的聚焦测试运行输出",),
       limitations=("单测通过只说明该批断言成立，不构成任何业务质量结论。",)),
    _m(metric_id="D02", title="版本与变更可复算",
       primary_dimension="传递保真度", dimensions=("传递保真度", "可观测性"), stage="开发与版本",
       definition="本次读数是否绑定代码、Prompt、Contract/profile、输入、模型策略与评测目录的真实版本/哈希；同一读数能否从指定产物只读重算。",
       numerator_spec="能唯一定位到版本/哈希的被评身份轴数",
       denominator_spec="本批声明的身份轴数",
       method="由指定产物只读重算并比对身份；缺版本或脏工作区须明示，不写「已封存」。",
       decided_by="AUTO", scope="本批开发与评测批次",
       required_evidence=("prompt 版本", "Contract 指纹", "输入绑定哈希", "模型身份", "git HEAD 与工作区状态"),
       limitations=("工作区非 clean 时必须如实标注，不得写成已 seal。",)),
    _m(metric_id="D03", title="缺陷闭环",
       primary_dimension="执行正确性", dimensions=("执行正确性", "可观测性"), stage="开发与版本",
       definition="每个实际失败是否定位到首个失败阶段、原因与影响范围，是否有修前反例、修后正例；未修问题留账。",
       numerator_spec="有完整证据链（首个失败点 + 反例 + 正例）的缺陷数",
       denominator_spec="本批记录在案的缺陷数",
       method="逐缺陷核证据链；不能用测试总数替代关闭结论。",
       decided_by="MIXED", scope="本批开发与评测批次",
       required_evidence=("缺陷清单", "反例测试", "前后对账"),
       limitations=("被评 run 里观察到的产品缺陷不等于本批已修；未修必须留账。",)),

    # ---- 上传与财务权威 ---------------------------------------------------
    _m(metric_id="P01", title="上传与权威绑定",
       primary_dimension="执行正确性", dimensions=("执行正确性", "传递保真度", "可观测性"),
       stage="上传与财务权威",
       definition="本 run 需要的六件输入中，身份与 SHA-256 都匹配的件数 ÷ 应收六件；缺件、多件、错件另列。财务三份 XLSX 还须逐份与既有财务快照 source_versions 同字节。",
       numerator_spec="身份与 SHA-256 都逐字节核对通过的输入件数",
       denominator_spec="本 run 声明的应收输入件数（三份 PDF + 三份 XLSX = 6）",
       method="重算本次绑定、暂存对象与快照声明的 SHA-256 并逐件比对；同字节复用不是本次重新抽表。",
       decided_by="AUTO", scope="本 run 六件输入",
       required_evidence=("run_input_binding.json", "financial_input_binding.json", "data/run_inputs/<run_id>/objects/*"),
       limitations=("绑定正确是前提，不等于内容充分。",)),
    _m(metric_id="P02", title="来源定位与回查",
       primary_dimension="传递保真度", dimensions=("执行正确性", "传递保真度", "可观测性"),
       stage="文档结构与树图",
       definition="实际正文引用中能回到本 run 已登记源文件及准确页/片段的引用数 ÷ 实际正文引用数；原 PDF 区域显示、缺陷与人确认另报。",
       numerator_spec="能解析到本 run 已登记来源身份/定位的引用键数",
       denominator_spec="正文实际使用的引用键数",
       method="逐引用核身份、定位与来源版本；能打开来源不等于句意被支持。",
       decided_by="MIXED", scope="本 run 公司节与财务节正文引用",
       required_evidence=("cited_prose.json 逐句 citations", "cited_input_manifest.json 的 citation_key→来源身份"),
       limitations=("定位存在是「可回查」，不是「语义被支持」；后者需人读或 Gold。",)),

    # ---- 文档结构与树图 ---------------------------------------------------
    _m(metric_id="B07", title="关键业务结构保真",
       primary_dimension="业务质量", dimensions=("传递保真度", "业务质量"), stage="文档结构与树图",
       definition="原文中经标注的关键标题—正文、表格行列—单位/脚注/续表、期间与来源角色关系中被正确保留的关系数 ÷ 适用关键关系数；另报跨标题拼接、错行错列、单位脱落。",
       numerator_spec="被正确保留的已标注关键关系数",
       denominator_spec="本样本已标注的适用关键关系数",
       method="逐关系对照原 PDF 与结构化产物；须有人工 Gold。",
       decided_by="GOLD", scope="公司节三份上传 PDF 的结构关系",
       required_evidence=("版本化 Gold 关系标注", "PageLayout/OutlineSpan/TableObject 产物"),
       limitations=("原 PDF 区域可展示不等于正式表格结构化合格，也不授权正文数字。",)),
    _m(metric_id="B08", title="树图可达与错关系",
       primary_dimension="业务质量", dimensions=("传递保真度", "业务质量"), stage="文档结构与树图",
       definition="有界导航内沿正确关系到达的 Gold 必需信息点数 ÷ Gold 必需信息点数；另报被裁定错误且可能误导取材的关系边数 ÷ 受评边数。",
       numerator_spec="沿正确关系可达的 Gold 必需信息点数",
       denominator_spec="Gold 必需信息点数",
       method="核父子、续表、表注、显式引用边及实际导航路径；建出节点/边不等于可达正确证据。",
       decided_by="GOLD", scope="公司节树/图导航",
       required_evidence=("Gold 路径或允许的等价路径", "导航 trace"),
       limitations=("无图边的演示切片标不适用，不伪报零错误。",)),

    # ---- Router 与混合检索 ------------------------------------------------
    _m(metric_id="B09", title="Router 合法路由",
       primary_dimension="业务质量", dimensions=("执行正确性", "业务质量"), stage="Router 与混合检索",
       definition="所选来源、工具及权威路径属于该 InformationNeed 的 Gold 允许集合的需求数 ÷ 受评需求数；另报应查未查、错路由、越权外检、普通 RAG 冒充财务权威。",
       numerator_spec="路由落入 Gold 允许集合的需求数",
       denominator_spec="受评 InformationNeed 数",
       method="比对需求、Router 决定与 SourcePolicy；允许多个正确路径，不强制唯一答案。",
       decided_by="GOLD", scope="本 run 研究侧需求",
       required_evidence=("InformationNeed 与 Router 决定落盘", "SourcePolicy", "Gold 允许路径"),
       limitations=("需求—决定落盘完整性可先核，正确率必须待 Gold。",)),
    _m(metric_id="B10", title="混合检索关键证据",
       primary_dimension="业务质量", dimensions=("执行正确性", "传递保真度", "业务质量"), stage="Router 与混合检索",
       definition="固定 k 与预算内命中至少一组合格等价证据的 Gold 必需信息点数 ÷ Gold 必需信息点数；另报前 k 结果相关精确率、首个可用证据排名与碎片污染。",
       numerator_spec="预算内命中合格等价证据的 Gold 必需信息点数",
       denominator_spec="Gold 必需信息点数",
       method="逐需求检查查询、候选、排序与实际读取的正确业务片段；页码命中不算片段命中。",
       decided_by="GOLD", scope="本 run 检索预算内",
       required_evidence=("查询/候选/排序 trace", "实际读取的 span", "Gold 等价证据组"),
       limitations=("命中正确页而未命中正确业务片段不算取得。",)),

    # ---- Harness 与 Topic Pack -------------------------------------------
    _m(metric_id="B11", title="Harness 正确闭环",
       primary_dimension="业务质量", dimensions=("执行正确性", "业务质量"), stage="Harness 与 Topic Pack",
       definition="按 Gold 应继续查的需求取得可用结果、应停止的需求有依据停止、未取得的需求诚实留 typed gap 的必需需求数 ÷ 受评必需需求数；结果、错误停止、无效重复/超预算分列。",
       numerator_spec="终态与 Gold 预期一致的必需需求数",
       denominator_spec="受评必需需求数",
       method="逐需求看调度、FollowUpNeed、预算、检索返回与预期终态。",
       decided_by="GOLD", scope="本 run Harness 调度",
       required_evidence=("Harness 动作/状态/预算", "FollowUpNeed", "Gold 预期终态"),
       limitations=("只有动作日志不能证明停止决策正确。",)),
    _m(metric_id="P03", title="关键材料取得",
       primary_dimension="传递保真度", dimensions=("传递保真度", "可观测性"), stage="Harness 与 Topic Pack",
       definition="被本次材料中至少一组合格等价证据覆盖的 Gold 必需信息点数 ÷ Gold 必需信息点数。",
       numerator_spec="被合格等价证据覆盖的 Gold 必需信息点数",
       denominator_spec="Gold 必需信息点数",
       method="运行 trace 与离线 Gold 对照；允许不同来源的充分组合，不以命中页码或材料份数替代。",
       decided_by="GOLD", scope="公司节三份上传 PDF",
       required_evidence=("材料清单", "离线 Gold"),
       limitations=("材料数量和页命中率不能替代取得率。",)),
    _m(metric_id="P04", title="事实资格去向",
       primary_dimension="执行正确性", dimensions=("执行正确性", "可观测性"), stage="Harness 与 Topic Pack",
       definition="有 typed 资格决定及依据的候选数 ÷ 实际候选数；再逐身份核合格事实是否进入 Pack/Writer、撤回值是否未进正文。",
       numerator_spec="有 typed 资格决定/依据的候选数",
       denominator_spec="本 run 实际候选数",
       method="候选、决定、Pack、清单与逐句核对对账；资格误放/误杀须用 Gold。",
       decided_by="MIXED", scope="本 run 事实候选",
       required_evidence=("候选对象", "资格决定", "Writer 清单", "逐句核对"),
       limitations=("记录守恒可计；决定本身对不对必须待 Gold。",)),
    _m(metric_id="B12", title="Topic Pack 素材充分",
       primary_dimension="业务质量", dimensions=("传递保真度", "业务质量"), stage="Harness 与 Topic Pack",
       definition="同时具备足够相关材料、所需合格硬事实、期间/来源角色以及重要限制与冲突的适用必需 aspect 数 ÷ 适用必需 aspect 数；不足但诚实留 typed gap 另算缺口诚实率。",
       numerator_spec="按 Gold 判为素材充分的适用必需 aspect 数",
       denominator_spec="适用必需 aspect 数",
       method="按 Gold 允许的多种充分证据组合逐栏目裁决；材料数、全文投递数不等于充分。",
       decided_by="GOLD", scope="company_business 适用必需 aspect",
       required_evidence=("当前 Pack", "Contract", "资格决定", "ResearchMaterialDisposition", "gap", "Gold/人工裁决"),
       limitations=("缺口诚实率不计入充分分子，也不得反过来冒充充分。",)),
    _m(metric_id="P05", title="Pack→Writer 清单守恒",
       primary_dimension="传递保真度", dimensions=("传递保真度",), stage="Harness 与 Topic Pack",
       definition="应送材料/事实集合与 Writer 实收集合逐身份比较，分别列缺失、额外、重复、过期。",
       numerator_spec="应送集合与实收集合逐身份一致的材料件数（集合相等时为全集大小）",
       denominator_spec="应送材料件数",
       method="用 Pack、manifest 与请求面重算差集；集合恰好一致只证明交接未丢。",
       decided_by="AUTO", scope="本 run 公司节/财务节 manifest",
       required_evidence=("cited_input_manifest.json", "cited_prose.json adoptions"),
       limitations=("交接守恒不证明 Pack 足够，也不证明 Writer 采用。",)),

    # ---- 写作与财务呈现 ---------------------------------------------------
    _m(metric_id="P06", title="阶段与调用留痕",
       primary_dimension="执行正确性", dimensions=("执行正确性", "可观测性"), stage="写作与财务呈现",
       definition="已完成且有对应产物的阶段数 ÷ 本次计划阶段数；每次模型调用的节、类别、模型、Prompt、预算、重试、状态与失败留存分别报。",
       numerator_spec="已完成且有对应产物的阶段数",
       denominator_spec="本 run 计划阶段数",
       method="核阶段日志、调用账、回复 journal 与产物；阶段事件不是可恢复 checkpoint。",
       decided_by="AUTO", scope="本 run 运行控制",
       required_evidence=("run_progress.jsonl", "cited_call_ledger.json", "cited_call_journal.json"),
       limitations=("缺 journal 时必须标注「回复原文不在盘上」。",)),
    _m(metric_id="P07", title="解析与逐句安全",
       primary_dimension="执行正确性", dimensions=("执行正确性", "可观测性"), stage="写作与财务呈现",
       definition="模型回复能否形成正式正文；在实际完成机械核对的句子中，有事实安全硬错的句数 ÷ 被核对句数，栏目覆盖问题另列。",
       numerator_spec="事实安全硬错句数",
       denominator_spec="实际完成机械核对的句数",
       method="逐句核数字、主体、期间、单位、引用与合格事实；解析失败后无正式正文，不能报「0 硬错」。",
       decided_by="AUTO", scope="本 run 各节正文",
       required_evidence=("cited_prose.json", "sentence_checks.json", "cited_report_version.json"),
       limitations=("机械合格只证明底线，不能宣称材料语义支持句子。",)),
    _m(metric_id="B01", title="公司必需栏目实答",
       primary_dimension="业务质量", dimensions=("业务质量",), stage="写作与财务呈现",
       definition="本 run 适用必需栏目中，确有在题、被来源支持的自然正文的栏目数 ÷ 适用必需栏目数；分列找到材料、取得权威、送达 Writer、写出、机械通过、人读认可。",
       numerator_spec="有在题且被来源支持的自然正文的适用必需栏目数",
       denominator_spec="适用必需栏目数",
       method="逐栏目分列六轴；有段落不自动等于实质回答，最后一步需标注或人工抽检。",
       decided_by="MIXED", scope="company_business 适用必需栏目",
       required_evidence=("Contract/演示范围栏目表", "manifest", "段落", "引用", "人读记录"),
       limitations=("前五轴可读；「实质回答」需人工或标注抽检。",)),
    _m(metric_id="B02", title="关键要点采用",
       primary_dimension="业务质量", dimensions=("业务质量",), stage="写作与财务呈现",
       definition="完整准确写入的已标注「应呈现」要点数 ÷ 本样本已标注应呈现要点数；另列重要遗漏与无意义重复。",
       numerator_spec="完整准确写入的已标注要点数",
       denominator_spec="已标注应呈现要点数",
       method="离线 Gold 与正文逐点对账；已引用材料份数不是该分数。",
       decided_by="GOLD", scope="本样本已标注要点",
       required_evidence=("离线 Gold 要点标注", "正文"),
       limitations=("待标注；不能用「已引用材料数」当内容质量分。",)),
    _m(metric_id="B03", title="数字与来源可信",
       primary_dimension="业务质量", dimensions=("传递保真度", "业务质量"), stage="写作与财务呈现",
       definition="每个正文高风险数值都由本句匹配业务、指标、期间、单位、口径及所需分母的合格事实授权；历史披露不写成当前状态。",
       numerator_spec="本句有合格事实授权的高风险数值数",
       denominator_spec="正文实际出现的高风险数值数",
       method="逐值机械核对加人工语义抽检；机械合格不能证明全部语义真实。",
       decided_by="MIXED", scope="本 run 正文数字",
       required_evidence=("sentence_checks.json 的 numeric_bases", "withheld_candidates.json", "合格事实"),
       limitations=("机械底线可计；语义忠实仍需人读。",)),
    _m(metric_id="B04", title="财务权威与分析",
       primary_dimension="业务质量", dimensions=("传递保真度", "业务质量"), stage="写作与财务呈现",
       definition="A1 指标表与 A2 确定性结构分开：权威格值一致数 ÷ 已展示格数；总量趋势、流/非流结构、≥15% 主科目、≥20% 变动、附注案例、偿债指标按适用项逐项标已呈现/缺口/未评。",
       numerator_spec="与权威事实逐格一致的已展示格数",
       denominator_spec="本 run 已展示格数",
       method="Decimal 复算格值；分析原因由人读；A2 零模型调用不等于零缺口。",
       decided_by="MIXED", scope="财务节 A1 与 A2",
       required_evidence=("financial/cited_metric_tables.json", "cited_balance_structure__fin_balance_structure.json", "FinancialFactPack"),
       limitations=("格值/结构可计；变化原因与分析质量不可由有数字推断。",)),
    _m(metric_id="B05", title="原表展示与人工确认",
       primary_dimension="传递保真度", dimensions=("传递保真度", "可观测性"), stage="展示与人工接受",
       definition="目标原 PDF 区域中，可显示数、缺陷数、获人确认完整清晰忠实的区域数分别报；人确认率＝获确认数 ÷ 目标数。",
       numerator_spec="获人工确认的目标区域数",
       denominator_spec="本 run 目标区域数",
       method="展示记录与真实人工确认分别核；页面能显示不授权正文格值，程序不得代签。",
       decided_by="MIXED", scope="公司节原 PDF 表格区域",
       required_evidence=("source_table_display.json 的 regions/registrations", "人工确认记录"),
       limitations=("展示数可计；人确认须人操作；正式 TS5 另轨。",)),
    _m(metric_id="B06", title="读者任务与人读 rubric",
       primary_dimension="业务质量", dimensions=("业务质量",), stage="展示与人工接受",
       definition="回答问题、重点、结构、客观中性、期间口径、缺口诚实、信用相关性各自 0–4；另测客户经理找出业务、财务变化、来源与未取得信息的正确率与耗时。",
       numerator_spec="逐项 0–4 分（无总分）",
       denominator_spec="不适用（分项评分）",
       method="版本化人读表、评阅人、样本、理由与时间；单份演示不宣称跨公司泛化。",
       decided_by="HUMAN", scope="本 run 报告",
       required_evidence=("版本化人工表单", "评阅人", "样本", "理由"),
       limitations=("待人工；不得由模型代填。",)),

    # ---- 独立审阅与放行 ---------------------------------------------------
    _m(metric_id="P08", title="独立审阅与状态",
       primary_dimension="执行正确性", dimensions=("执行正确性", "传递保真度", "可观测性"),
       stage="独立审阅与放行",
       definition="真实独立审阅覆盖句数 ÷ 可审正文句数；另列审阅产出者、意见、blocking、A2 不适用、报告级审阅是否运行。预览、系统放行、人工接受互不推导。",
       numerator_spec="有本 run 真实独立审阅覆盖的正文句数",
       denominator_spec="本 run 可审正文句数",
       method="意见必须绑定本稿版本及本 run 的真实调用；issues=[] 只表示本次未报问题。",
       decided_by="AUTO", scope="本 run 各节审阅",
       required_evidence=("review_issues.json", "cited_call_ledger.json", "cited_report_version.json"),
       limitations=("审阅覆盖与状态可计；检出率、误报率需正反两类 Gold。",)),
    _m(metric_id="B13", title="审阅检出与不过度",
       primary_dimension="业务质量", dimensions=("业务质量",), stage="独立审阅与放行",
       definition="意见精确率＝独立裁决有效且可行动的意见数 ÷ 全部意见；过度审阅率＝被裁定无依据、重复、机械门无增量复述或严重度夸大的意见数 ÷ 全部意见；误阻断率＝把 Gold 无阻断问题判为 blocking 的案例数 ÷ 受评无阻断案例数。",
       numerator_spec="经独立裁决有效且可行动的意见数（精确率分子）",
       denominator_spec="已提出意见数",
       method="同时准备有缺陷和无缺陷两类盲测样本，与真实缺陷召回率并列；无 Gold 时只能显示意见数。",
       decided_by="GOLD", scope="本 run 独立审阅意见",
       required_evidence=("冻结正文", "审阅意见", "正反两类盲测 Gold", "独立裁决记录"),
       limitations=("没有裁决样本时不得显示精确率、误阻断率或「审阅通过」；靠沉默换高精确率不算。",)),
)

#: 按 ID 取指标。
BY_ID: dict[str, MetricSpec] = {m.metric_id: m for m in METRICS}

#: 目录自证：24 个指标、ID 唯一、维度与环节都取自受控词表。
def assert_catalog_complete() -> tuple[str, ...]:
    """在用它之前自证目录完整且自洽。不成立即抛，宁可在写出结果之前停住。"""
    ids = [m.metric_id for m in METRICS]
    if len(ids) != len(set(ids)):
        raise ValueError(f"指标 ID 有重复：{sorted(ids)}")
    if len(METRICS) != 24:
        raise ValueError(f"指标数是 {len(METRICS)}，评测文档写的是 24 个")
    for m in METRICS:
        if m.primary_dimension not in DIMENSIONS:
            raise ValueError(f"{m.metric_id} 的主归属 {m.primary_dimension!r} 不在四个维度里")
        if not set(m.dimensions) <= set(DIMENSIONS):
            raise ValueError(f"{m.metric_id} 的维度 {m.dimensions} 超出四个维度")
        if m.primary_dimension not in m.dimensions:
            raise ValueError(f"{m.metric_id} 的主归属不在它自己的维度列表里")
        if m.stage not in STAGES:
            raise ValueError(f"{m.metric_id} 的环节 {m.stage!r} 不在八个环节里")
        if m.decided_by not in DECIDERS:
            raise ValueError(f"{m.metric_id} 的判定来源 {m.decided_by!r} 不在受控词表")
    return tuple(ids)


__all__ = [
    "CATALOG_VERSION", "DIMENSIONS", "STAGES", "DECIDERS", "MEASUREMENT_STATES",
    "EXECUTION_STATES", "MetricSpec", "METRICS", "BY_ID", "assert_catalog_complete",
]
