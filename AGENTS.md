# AGENTS.md

> **Project constitution. Read this file completely before changing code or project documents.**
> When instructions conflict, follow the authority order below. Do not use a historical report or runtime Prompt as an implementation instruction.

## 1. Project snapshot

| | |
|---|---|
| Product | A-share listed-company credit analysis report generator |
| Purpose | Local Streamlit interview demo; not a production credit decision system |
| Primary user | Corporate banking relationship manager / interviewer |
| Demo subject | CATL (300750), without company-specific production rules |
| Priority | 演示稳定 > 亮点突出 > 功能全面 > 工程严谨；任何亮点不得以错误事实、错误数字或伪造引用为代价 |
| Current stage | Dual track: formal P3R/P4R tree gate remains open; the approved 2026-09-30 Demo Backbone vertical slice is the current time-boxed execution lane |

The current design is [DESIGN_V2.md](./DESIGN_V2.md). The original [DESIGN.md](./DESIGN.md) is a historical V1 baseline only.

## 2. Documentation authority

Read [DOCUMENTATION_INDEX.md](./DOCUMENTATION_INDEX.md) for the full map. The binding order is:

1. `AGENTS.md` — engineering and safety constitution.
2. `DESIGN_V2.md` — current product, business, and architecture design.
3. Confirmed business baselines — frozen Contract v2 (`templates/contracts/standard_v3.yaml`), Source Policy, WritingSpec, PresentationProfile, `FORMULA_REVIEW.md`, plus immutable v1 compatibility assets (`templates/contracts/standard_v2.yaml`, `contracts/sc_decisions.yaml`) within their declared scope.
4. `V2_IMPLEMENTATION_PLAN.md` — stage order and exit gates.
5. `2026-09-30_DEMO_BACKBONE_MILESTONE.md` — approved time-boxed vertical-slice scope; it may authorize representative downstream interfaces but never close or weaken a formal stage gate.
6. The current umbrella task — `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`; `TREE_STRUCTURE_ADJUSTMENT_TASK.md` continues to govern the open formal tree gate.
7. `V2_TODO.md` — progress record only.
8. `CLAUDE.md` — thin Claude Code bootstrap only.

Historical task books, acceptance reports, generated reports, debug files, reference documents, and `llm/prompts/*.txt` never override this chain.

## 3. Active architecture gate

Phase 2/3 historical runs, gold, split manifests, and frozen results remain immutable safety and regression baselines. They do **not** prove that a complete topic can be written from the current P3 output.

**现行设计裁决（2026-10-01，优先于本节下方的旧写作链描述）：**研究侧唯一 Harness、Evidence/Pack、事实资格、财务权威和冻结业务资产不变。Pack 之后改为“完整当前材料与合格事实 → Writer 直接写逐句引用的自然正文 → 确定性逐句硬核对 → 独立只读语义审阅 → 逐句问题预览 → 分开的系统放行与人工接受”。Writer 不再必须同时产出 `ClaimCandidate`/支撑提案；Claim 审核账不得充当拼文素材。硬事实必须有相应合格事实权威。**本次演示的主营业务原表改由已登记电子 PDF 的人工确认区域只读展示**，只确认原件可见，不成为 `TableObject`、Pack/Writer 材料或数字权威；正文使用的营业收入、成本、毛利及比较数字仍逐格资格化并由 Python/Decimal 计算。财务表继续由权威财务事实确定性生成。旧写作链和旧逐表签发裁决留作历史/正式能力轨，不再是本次演示前置。完整规则及状态边界见 `DESIGN_V2.md` §0.20–§0.21。**设计与演示验收已调整，不表示实现或真实验收通过。**

The following is the **historical implemented writing chain, retained for compatibility and audit, not the current target production chain**:

```text
SectionContract / SectionTask
  → Worker orchestration shell
  → Harness-owned TopicResearchState
  → (InformationNeed → Router → existing atomic executor → ToolRegistry)*
  → immutable EvidenceBlock provenance
  → versioned PageLayout / read-only DocumentOutline
  → OutlineSpan / TableObject inspection + bounded fallback expansion
  → fact/source validation
  → ResearchMaterial (retained intact)
  → high-risk fact pre-validation (non-exhaustive)
      → FactCandidate
      → versioned FactQualificationDecision
          ├─ eligible → qualified SupportedFact
          └─ rejected → typed rejection decision / audit (source material retained intact;
             a gap/block follows only if a Contract-required aspect/fact is still unmet)
  → authoritative TopicResearchPack successor
      (pre-validated facts enter the Pack; writing-side path-B SectionClaims never do)
  → exact Writer material-context manifest
  → SectionDraft (pre-gate; MUST NOT reference SectionResult or future decisions)
      ClaimCandidate[] (one atomic assertion each)
      + NarrativeDraftUnit[]
      + ProposedSupportRef[] (targets a candidate or draft narrative unit; no future decisions)
  → deterministic Claim Binding Gate
      → one aggregate ClaimBindingDecision per binding-subject revision over the complete ordered proposal set
      ├─ factual candidate → Section Evaluator → one ClaimEntailmentDecision per candidate revision
      │    → factual AcceptedSupportBinding[] → accepted SectionClaim[]
      └─ context narrative unit → context AcceptedSupportBinding[] (no entailment decision)
  → natural NarrativeSentence / Paragraph / Table + Unresolved
      (accepted Claim IDs + context accepted-binding IDs)
  → SectionResult (post-gate; one-way reference to SectionDraft)
  → deterministic assembly
  → Assurance Controller orchestration
      ├─ deterministic hard gates
      ├─ isolated read-only Review Agent → ReviewIssue[]
      └─ deterministic version-bound aggregation
  → separate human-acceptance status
```

Rules below describe the historical chain where they refer to `ClaimCandidate`, path A/B, binding, entailment, accepted bindings, or Claim-based Narrative. Those writing-side rules are **superseded for the new chain** by `DESIGN_V2.md` §0.20; their research-side material retention, fact qualification, exact provenance, financial authority and safety constraints remain binding:

- `ResearchOutcome` is one atomic research record and a compatibility-evaluation object. It is not P4's sole content input.
- `EvidenceBlock` is an immutable provenance/citation anchor, not a reliable business boundary, paragraph, table, or completion unit. Historical Evidence IDs and Evidence Sets are never rewritten to simulate semantic structure.
- Every supported electronic PDF has a versioned `PageLayout` and read-only `DocumentOutline` derived from the raw PDF or the same canonical layout source. Bookmarks and table-of-contents entries are candidates; body headings, including small subheadings, provide the confirming anchors.
- Local RAG, `TopicResearchPack`, and P4 consume `OutlineSpan` and `TableObject` material units. They may cite the parent Evidence plus exact span/page locators, but must not promote a cross-heading whole Evidence block as one semantic material.
- Contract-to-outline title/synopsis similarity is candidate navigation only. It never proves an aspect covered; coverage still requires qualified material, facts, citations, authority, and the Contract completion rule.
- Adjacent-block/page expansion, rolling frontiers, and explicit-reference traversal remain bounded fallbacks for unavailable/low-confidence outlines or cross-node references, not the normal source-boundary algorithm.
- Every fallback result still becomes a precisely located `OutlineSpan`; the whole `EvidenceBlock` never becomes a formal material. Fallback, low-confidence, or `unassigned` spans may support candidate facts, but cannot by themselves establish `set_complete` without a verified outline/table boundary.
- Navigable nodes use deterministic, extractive, source-linked synopses. Aspect-to-node queries come from a versioned, company-independent navigation profile derived from the frozen Contract; synopsis/profile versions enter dependency fingerprints and neither can serve as evidence.
- Harness owns the only authoritative `TopicResearchPack`, topic state, aspect scheduler, cumulative budget, and checkpoint.
- `sections.topic_research` and similar experimental code may contribute pure algorithms but must not become a second Router/Harness/tool/LLM runtime.
- Material retention, fact qualification, and claim acceptance are **three separate identities**: `ResearchMaterial`, `FactCandidate`/`SupportedFact`, and `ClaimCandidate`/`SectionClaim`. Rejecting a fact candidate must never delete, fragment, rewrite, or downgrade its source material. Wire-adopted candidates are not thereby qualified facts, and old Packs/runs stay historical read-only.
- The writing chain is **strictly acyclic**. `ClaimCandidate` identity contains the atomic proposition and task/section/company/`report_as_of`/Contract/draft revision, **not** support/material/locator fields. `ProposedSupportRef` owns source binding and may target either a factual `ClaimCandidate` or a context `NarrativeDraftUnit`; it must never reference future decisions, accepted objects, `SectionClaim`, final Narrative, or `SectionResult`. Exactly one aggregate `ClaimBindingDecision` exists per `(binding_subject_kind, binding_subject_id, draft_revision)` and binds the complete, deterministically ordered proposal IDs/hashes set, its digest, and per-edge mechanical results. Exactly one `ClaimEntailmentDecision` exists per factual candidate revision and binds the one passing aggregate decision and the same support-set digest. One `AcceptedSupportBinding` follows each accepted proposal: factual variants reference both decisions; context variants reference only the mechanical decision and forbid an entailment decision. `SectionClaim` later references factual accepted-binding IDs; final Narrative later references accepted Claim IDs and context accepted-binding IDs. Earlier objects never reference later ones, and no object includes its own or a successor ID in its own content identity. `SectionDraft` is pre-gate and never references `SectionResult`; post-gate `SectionResult` references `section_draft_id` one way.
- Rejection is **decoupled from gap formation**, and the two are two independent records that must never be written as alternatives to each other. A rejected `FactCandidate` or `ClaimCandidate` **always** produces a typed, versioned rejection decision/audit — the rejection decision is never replaced by a gap, and rejecting one candidate must never automatically manufacture a gap. Only when the Contract-required aspect or fact is still unmet *because of* that rejection does a ContractGap/Block **additionally** arise, bound to its own Contract basis. Never use phrasings such as `rejected → reason/gap/audit`, "qualification decision or gap", or "audit/gap destination".
- Fact handling has **two channels**, and neither replaces the other. Channel A (research side) pre-validates the high-risk hard facts a Contract requires before the Writer runs — amounts, ratios, dates, periods, currency, units, financial figures, credit-line amounts and their semantic classes, checkbox states, table row/column relations, legal entities, and explicit negative facts — and it is deliberately **non-exhaustive**: it does not pre-split the whole document into facts. Channel B (writing side) lets the Writer propose descriptive `ClaimCandidate`s grounded in the complete material set. The Writer may never approve its own candidates, write them back into or contaminate historical Packs, smuggle new hard facts past pre-validation, generate its own authority, or self-assess.
- Company and industry writers consume a **complete and exact material set**: the union of `materials` across all current Packs in the `VerifiedPackSet`. Every manifest member has a verifiable `WriterMaterialProcessingDisposition` traceable to its Pack-side `ResearchMaterialDisposition`; the former records processed/used/not-used and support usage, while the latter records only research admission/retention/source validation and never fact eligibility or Writer usage. Unused material is legal and is not a gap; a required Contract fact not obtained creates an explicit gap/block and must never be disguised as "not used". Missing, duplicate, stale, wrong-task, wrong-company, wrong-`report_as_of`, or wrong-Contract Packs block. The financial writer consumes authoritative `FinancialFactPack` plus validated Evidence-backed note facts.
- Every support edge declares its authority kind (`topic_pack` / `financial_pack` / `evidence_note` / `external_snapshot`) and its semantics (`factual` / `context`). The authority dimension is a **closed tagged union**: each of the four kinds specifies required / optional / forbidden fields for path A, path B, and context in `DESIGN_V2.md` §0.12 item 6 — a principle sentence alone does not close it, and any field combination outside that table fails closed. **Authority container identity** (the Pack / `FinancialFactPack` / `FinancialSnapshot` / note artifact / `ExternalSnapshot` record) and **source/provenance identity** (the underlying document / Evidence / external URL with body hash / SourcePolicy and date) are **separate** and must both appear in identity and disposition; never let one `ExternalSnapshot` impersonate container and fact identity at once.
- There are exactly **two lawful factual-support paths**, both declaring `support_semantics = factual`. **Path A** binds an authority-specific pre-validated fact identity (`SupportedFact`, `FinancialFact`, Evidence note fact, or `ExternalFact`) plus the container, source/provenance, payload and exact locator required by its tagged variant. Only `topic_pack` necessarily requires `ResearchMaterial`; other variants must not invent it. Path A is available to any atom with a lawful fact identity and is the only path for high-risk hard facts. **Path B** binds exact Pack `ResearchMaterial`/payload/locator plus the two decisions and is only for non-high-risk descriptive atoms. `ExternalSnapshot` is a source carrier, never a fact authority: an `ExternalFact` must bind its qualification decision, snapshot/body hash, SourcePolicy, dates, proposition, and locator, and must enter the current Pack authority-input/disposition/output union. If external text is formally adopted as Pack material, it uses `topic_pack` path B while retaining external provenance.
- Context support has no fact ID and no `ClaimEntailmentDecision`; it targets a stable `NarrativeDraftUnit`, never a Claim. It must be explicitly typed and carry the container, source/provenance, payload and locator required by its authority variant. It may support only background, structure and cohesion, and may not authorize a factual Claim or add numbers, entities, periods, causality or conclusions. `ClaimSupportRef` is only the compatibility tagged-union name for `ProposedSupportRef | AcceptedSupportBinding`, not a third wire object. Their unique stage-specific field lists live in `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §6.3. Missing/ambiguous role or a field combination outside the closed authority table fails closed.
- The Writer never retrieves and never goes online. Harness owns `InformationNeed → Router → ToolRegistry`, research scheduling, budget, checkpoint, the external research funnel, and the only authoritative Pack. When the Writer needs more, it may only emit a structured `FollowUpNeed`; Harness judges it against Contract/budget/SourcePolicy, executes the follow-up or the network call, forms a new Pack, and the Writer performs a bounded rewrite. External facts still require a fetch, a non-empty body, an immutable snapshot, SourcePolicy checks, a date, and a locator. "Network" is a source type, not "non-fact".
- Persistence has **four separate boundaries**. (1) Research/Pack persists `ResearchMaterial`/`ResearchMaterialDisposition`, fact candidates/qualification/results, `ExternalFact` plus snapshot refs, research gaps/conflicts/not-found and provenance. (2) Writer/Section/Narrative persists the exact manifest, `WriterMaterialProcessingDisposition`, `FactNarrativeDisposition`, Claim/Narrative draft subjects, proposals, both decisions, accepted bindings, `SectionClaim`, final Narrative and unresolveds. (3) `FollowUpNeed` is an independent run/trace; Harness executes it into a new Pack without rewriting the old. (4) Review/Assurance persists `report_version`-bound issues/status. No single successor carries all four. The two material dispositions have different fields and semantics, and `FactNarrativeDisposition` is a third writer-side record; none may impersonate another.
- Those four boundaries are divided by **three dependency/content identity sets**, a different axis. Pack qualification governs research material dispositions, candidates/decisions/results, `ExternalFact` and research gaps. Section/Writer/Evaluator governs the manifest, writer material/fact narrative dispositions, Claim/Narrative draft subjects, proposals, both decisions, accepted bindings, claims, final Narrative and unresolveds. Review/Assurance governs `report_version`-bound issues/status. `FollowUpNeed` lies outside all three. A version change invalidates only its set; historical objects are not rewritten, while dependent downstream objects lose current eligibility and must obtain fresh decisions.
- A deterministic **Claim Binding Gate** runs before semantic evaluation. It is mechanical, has no long-text semantic judgement or review LLM, and never infers semantics from `fact_id is None`. For each binding-subject revision it recomputes the complete ordered proposal set/digest, rejects missing/duplicate/extra proposals, validates each edge's schema/version/manifest/authority/container/provenance/role/semantics/path/payload/locator/citation and high-risk authority, and emits exactly one aggregate `ClaimBindingDecision` with per-edge results. One edge failure fails the aggregate. A pass proves only mechanical binding and never impersonates entailment.
- **Atomic model**: one `ClaimCandidate` expresses exactly one atomic assertion. A mixed natural sentence **must be split into multiple `ClaimCandidate`s** — each high-risk atom independently on path A, each descriptive atom independently on its lawful path. Never claim "per-atom binding inside a single mixed Claim" while the wire carries no atom ID, and **do not add a `ClaimAtom` system** unless a read-only audit proves the existing atomic-Claim constraint genuinely cannot express this rule. A `NarrativeSentence` may bind several accepted `SectionClaim`s.
- P4 keeps atomic auditable Claims, produces human-readable paragraphs/tables supported by multiple Claims, and may bind several Claims to one natural sentence. Entailment is checked role-aware: every factual atom must be entailed by a qualified Claim/fact and its factual support edge — either a path-A pre-validated authority or a path-B exact material plus an accepted `ClaimEntailmentDecision` — while context material may only validate background, structure, and cohesion and can never fill a missing qualified fact. Because candidates are atomic, a mixed sentence is expressed as several candidates rather than one Claim carrying per-atom authorizations. Sentence text and its bindings share one content identity — changing either invalidates the other and forces re-checking. The writer never self-assesses.
- Phase 4 **Section Evaluator** is the sole Claim-level semantic verifier. It receives only factual candidate revisions with one passing aggregate binding decision and an identical complete support-set digest; context narrative subjects never enter Claim entailment. Exactly one versioned `ClaimEntailmentDecision` is emitted per candidate revision, binding that aggregate decision, support digest, designated authority/material set, and rubric/prompt/model versions. It applies to path A and B factual candidates; authority still comes from the path-specific source. A pass yields factual accepted bindings and then `SectionClaim`; failure yields bounded rework, non-factual transition, unresolved or independently conditioned Contract gap/block. The decision is writer-side, never Pack content. Evaluator is isolated from Writer, does not repeat mechanical ID/hash/locator checks, outputs structured decisions only, and is not final report approval.
- The **Independent Review Agent** is a separate, read-only call/context that runs after chapter finalization and deterministic assembly and emits only `ReviewIssue[]`. It checks meaning change when merging multiple correct Claims, selective use of material, missing key limitations, local-to-whole extrapolation, correlation written as causation, exaggerated advantage or risk, cross-Claim/chapter contradictions, and whether the whole could mislead credit staff. It must not rewrite the report, add research, go online, repeat the per-item hash/locator gates, override a hard failure, release itself, or rewrite the `ClaimBindingDecision` / `ClaimEntailmentDecision`. Its `ReviewIssue[]` and the aggregate status are `report_version`-bound and belong to the Review/Assurance identity set, never to a Pack.
- Phase 5 Assurance Controller controls only system-Assurance/release eligibility: deterministic gates first, evidence-grounded semantic review second, and a deterministic version-bound status aggregation. An LLM may not override hard failures, rewrite the report, or approve its own output by self-assertion. Process completion, preview availability, system-Assurance status, final human acceptance, and the formal-phase-closure governance snapshot stay separate states. Final human acceptance is never inferred by the Controller.
- Formal Phase 5 closure remains blocked until the P3R/P4R content gate passes. The approved 2026-09-30 Demo Backbone may exercise a representative Writer → independent reviewer → deterministic Controller → read-only UI slice on the same production interfaces; this exception never grants formal phase closure or release eligibility.

## 4. Hard constraints

- No scanned PDF or OCR in the first release. Electronic PDFs must have an extractable text layer; low quality fails fast.
- No PPT, image, Word, or arbitrary office-document input in the first release.
- Financial input may be electronic PDF, Excel, or both. Financial PDF numbers must be structurally extracted, reconciled, and conflict-checked before use; ordinary RAG cannot authorize financial numbers.
- The LLM never calculates amounts, ratios, growth rates, comparisons, or table aggregates. Python/SQL with Decimal semantics calculates them.
- Do not invent a single merged authority for all numbers. FinancialSnapshot and Evidence-backed note facts retain their own authority/version checks; formal `ExternalFact` retains external-fact authority/version checks, while `ExternalSnapshot` separately retains only source-carrier integrity/version checks. A unified Fact Registry is a read model and semantic identity layer.
- `TableObject` is a structured Evidence-backed material and navigation object. It does not become a `FinancialSnapshot`; main statements, Evidence-backed note facts, ordinary business tables, and external facts retain separate authority checks. Tables split into three downstream destinations: (1) financial main statements, note number tables and calculation-needed tables keep their `TableObject` reading/retrieval view but separately pass structured numeric authority, Decimal calculation, and caliber-conflict checks — the LLM never calculates; (2) business tables (directorships, subsidiaries, use of proceeds) stay structured `TableObject` writing material and are not forced into the calculation layer; (3) checkbox/disclosure-template/layout tables become typed form or selection state, or a gap — headers, options, units, and layout fragments must never be promoted to company facts. A table's reading view and numeric view share source identity and locator.
- `DESIGN_V2.md` §0.21 的原 PDF 表格区域仅为本次 Demo 的**来源回查展示**例外：保留文档版本/哈希、页与区域及人工确认，不是 `TableObject` 读写资格、Pack 材料或数字权威；不能用截图/OCR 为 Writer 授权数值，财务权威与正式 TS5 门不受此例外改变。
- No user authentication, encryption, multi-user isolation, production concurrency, or disaster recovery unless a later approved task explicitly adds them.
- No module-specific branch for `300750`, CATL, a case id, gold page, answer keyword, or fixed document page.
- No gold document/page in runtime query planning, retrieval decisions, sufficiency, stopping, answer generation, or grading. Gold is offline diagnosis only.
- Missing evidence means “not obtained within the searched scope,” never “does not exist,” unless an authoritative source explicitly supports the negative fact.
- Search snippets and URLs are navigation only. External facts require fetched non-empty content, immutable snapshot, authority/date/source-policy checks, and citations.
- Current external search runtime is Bocha. Tavily is not a runtime dependency or fallback and must not be silently enabled.
- Do not weaken fail-closed correctness to improve FULL or completion rates. Safety passing also does not prove content completeness.

## 5. Research and writing semantics

- Formal Contract `required_aspects` are the scheduling and completion units. One broad retrieval can cover multiple aspects; only gaps trigger focused follow-up.
- An atomic `ANSWER` ends the current need, not the whole Topic.
- After a relevant hit, first resolve the smallest sufficient outline node/subtree and load its `OutlineSpan`/`TableObject` members. Follow typed table-continuation and explicit-reference relations when needed. Only when structure is unavailable or low-confidence may the system use bounded adjacent-block/page expansion; stop at unrelated nodes, unresolved structure, no-new-information boundaries, or hard budget.
- Outline navigation metadata and deterministic synopses may improve candidate ranking, but are not Evidence and cannot be cited as facts.
- PageLayout-to-Evidence character alignment is versioned and auditable. Ambiguous alignment fails closed or produces a new append-only Evidence Set; offsets must never be guessed.
- Preserve every validated relevant material/fact in the Pack even if a short answer omitted it.
- Topic budgets are adaptive but finite and versioned. Never solve completeness by globally increasing top-k or tool calls, and never tune budgets by company/case.
- External research uses an auditable funnel: intent → candidates → rank → fetch → automatic snapshot → extract → validate → adopt/reject.
- **Current writing semantics (2026-09-29, design approved, implementation pending):** Writer reads the complete exact current Pack and qualified facts, writes natural sentences with current-manifest member references, and does not create/approve atomic Claim proposals as a prerequisite to prose. Deterministic checks verify references and high-risk authority, especially financial figures and original-table row/column/cell facts; independent read-only review flags semantic problems. Failure is marked per sentence and retained in a clearly non-publishable preview rather than erasing the section. The old `ClaimCandidate`/path-B/entailment rules in the following bullets describe the historical runtime only; they do not instruct implementation of the new writing path. See `DESIGN_V2.md` §0.20.
- P4 writers organize supported facts and complete materials into a readable business narrative. They may order, merge, compose, and add non-factual transitions. The accurate prohibition is: a writer must never fabricate a fact, number, entity, period, or conclusion that the material or an authority does not contain, and must never introduce a high-risk hard fact by bypassing pre-validation. It is **not** forbidden for a writer to propose a descriptive `ClaimCandidate` that a designated material atomically entails; that is path B, and it is authorized by the exact material plus an accepted `ClaimEntailmentDecision` rather than by a pre-existing `SupportedFact`. Each candidate expresses exactly one atomic assertion; a mixed sentence becomes several candidates.
- Research-side fact qualification is a **non-exhaustive** pre-validation of Contract-required high-risk hard facts, not a full pre-split of the document. The Writer then proposes descriptive `ClaimCandidate`s from the complete material set, proposes their support edges, and may emit a structured `FollowUpNeed`; a candidate is not a fact, a proposal is not an acceptance, and the Writer never approves, writes back, or self-assesses. High-risk hard facts (amounts, ratios, dates, periods, currency, units, financial figures, credit-line amounts and semantic classes, checkbox states, table row/column relations, legal entities, explicit negatives) must never enter the body by bypassing pre-validation. Qualification policy version belongs to the Pack dependency fingerprint; research-side candidates, qualification decisions, results, `ResearchMaterialDisposition`, and gaps belong to Pack content identity. Writing-side `ClaimCandidate`s, `ProposedSupportRef`s, `ClaimBindingDecision`s, `ClaimEntailmentDecision`s, `AcceptedSupportBinding`s, `SectionClaim`s, narratives, and unresolveds belong to the Writer/Section/Narrative successor instead — they are never written into a Pack and never counted in Pack content identity. Never confuse the research-side and writing-side identities, and never merge the four persistence boundaries.
- Each disposition must be recomputable or independently verifiable under its own semantics: `ResearchMaterialDisposition` for research admission/retention/source validation, `WriterMaterialProcessingDisposition` for manifest processing/usage, and `FactNarrativeDisposition` for presentation of pre-validated facts. A writer's self-reported not-used reason does not establish any of them.
- **Three date axes are kept apart; one never stands in for another.**
  (a) The **fact-applicability period** — when a flow/activity/event held (“2025年度” / “2025年内”) or when a balance stood (“截至2025年12月31日”) — is a high-risk hard fact. It comes only from a pre-validated authority fact (path A) or verbatim from the exact material’s own wording, it is never guessed, and it is never back-filled from another date field. An undefined “报告期内” is not a period.
  (b) The **source attribution** — which material, which version, which page, and which **verifiable disclosure date** — tells the reader “this is what that material disclosed”. It is attached **deterministically from the registered source identity**; a writer never types it.
  (c) `report_as_of` is the **report generation date** and is never a fact period and never a disclosure date.
  A non-numeric general business description (products, business model, procurement/production/sales mode) does **not** have to mechanically repeat “2025年度” in every sentence, but it must not be written as “as of the report generation date this still holds”, nor as “this has always been so”. Content supported only by an older material keeps a historical source attribution or becomes a gap; where newer and older materials substantively differ, similar wording must not smooth the difference away. A disclosure date that cannot be verified is marked unknown — the upload time, PDF metadata, and the financial period end never stand in for it.
- News and externally fetched events prefer a supported **event-occurrence / effect date**. When only a report’s publication date is available, the body may say only “a report published on <date> mentioned …”; the publication date is never presented as the date the event occurred.
- Optional or diagnostic financial metrics that cannot be computed may stay out of the main body according to a versioned display policy; required gaps remain explicit.
- Report length follows Contract coverage and information density. There is no 8,000-character hard cap; 20,000–30,000 Chinese characters is only a human reference for a fuller report, not a pass condition.

## 6. Implementation discipline

### Plan before code

Before a new module or cross-module refactor, state:

- public input/output types;
- main internal functions;
- dependencies and authority boundaries;
- CLI/self-check command;
- tests, migration effects, commit split, and stop condition.

Do not code a major batch until its plan is reviewed when the active task requires a review gate.

### Protect the workspace

- Inspect `git status` first. Existing tracked or untracked changes belong to the user or another agent.
- Do not overwrite, delete, reset, stash, or silently include unrelated changes.
- Never commit `.env`, API keys, databases, logs, generated results, debug files, personal settings, or reference documents.
- SQLite schema changes are append-only migrations. Historical migrations and accepted run artifacts are immutable.
- Read-only inspection paths must not initialize, create, migrate, or mutate databases.

### One responsibility per change

- Each commit has one reviewable responsibility; tests may accompany the responsible module.
- Do not mix governance documents, runtime logic, generated results, and unrelated cleanup.
- Reuse existing public interfaces. If an interface must change, version it and provide compatibility/migration tests.

### Runtime observability

- Core modules need a CLI or deterministic self-check unless the approved task explicitly classifies them as pure private helpers.
- All retrieval goes through the approved retrieval/ToolRegistry path and writes retrieval trace.
- All LLM calls go through the shared client and write call id, prompt/version, model, latency, usage when available, completion status, and errors.
- Prompts live in `llm/prompts/*.txt`; do not inline them in Python.
- Do not expose hidden chain-of-thought. Persist actions, evidence, decisions, and concise reasons only.

### Testing

- Add regression tests for every fixed defect.
- Run focused tests first, then the full `python -m evals.run_evals` gate before a code commit.
- Mock tests prove deterministic behavior; at least one bounded formal-chain integration test proves orchestration; small real vertical slices prove content usefulness.
- Tree-structure tests separately measure body-heading recall/precision, hierarchy accuracy, cross-heading span purity, unmapped text, table integrity/continuation, outline-aware retrieval, Pack retention, and P4 material consumption. Page-level retrieval recall alone cannot close this gate.
- A green test count is not a product-quality result. Report separately: safety, aspect coverage, material/fact retention, external-funnel yield, narrative readability, and unresolved gaps.
- Never rerun frozen evaluation splits to tune them. New refactor evaluation uses new versioned fixtures/runs.

## 7. UI and demo boundary

- `streamlit_app.py` remains thin: input, service invocation, progress, artifact loading, and display only.
- Prefer loading a persisted real artifact for screenshots and recording; do not spend LLM/network calls merely to view an existing run.
- The current interview-demo release is read-only after generation: it shows actual status, missing information, searched scope, impact, failure reasons, and suggested future material types, but does not implement user supplementary upload, gap-to-material binding, Evidence mutation, or user-triggered resume/continue. Keep structured extension seams without exposing inactive actions.
- Status is a product feature, not a decorative percentage. Distinguish the four report-version-bound states—process completion, preview availability, system-Assurance outcome, and final human acceptance—from the separate read-only governance snapshot for formal phase closure; progress must come from persisted units/artifacts rather than model estimates.
- Demo may focus on CATL, but code, Contract, policy, and tests must remain company-independent.
- The 2026-09-30 Demo may be content-representative rather than comprehensive. It must nevertheless expose the complete design flow, use real persisted artifacts or explicit gaps, and keep `formal_phase_closure` separate from preview availability.
- **Demo topic source tables (2026-10-01 adjudication, `DESIGN_V2.md` §0.21).** For `company_business`, the M930-3 **demo-content** gate requires both substantive, cited operating prose and human-confirmed, unaltered **original PDF table regions** relevant to annual-report revenue composition, operating costs and gross margin. Each region binds the registered PDF hash/version, page/rectangle, continuation when present, rendering hash and a human confirmation record. This is a separate read-only source panel, **not** a qualified `TableObject`, Pack/Writer member, table-cell authority or Contract `set_complete` proof. The 18 aspects × three uploaded documents are still audited separately for source responsibility, actual read, Pack/Writer destination and gaps; prospectus material is not forcibly adopted to meet a table count. A source table that exists but cannot be located or faithfully shown is a **system display capability defect**, not a source gap, and the demo-content gate cannot pass. Only a verified absence in the source is a source gap. Any revenue/cost/margin/share/comparison used in prose independently requires qualified row/column/period/unit/caliber/cell facts and deterministic calculation. Diagnostic flat text and `refused`/`partial` table objects remain ineligible. This changes only the **demo acceptance standard** previously recorded in §0.17; the formal TS5 structured-table gate, frozen Contract, history, and system/human release gates remain open and unchanged.
- **Historical structured-table adjudication (2026-09-29, `DESIGN_V2.md` §0.19):** Independent table-local completeness without a document-wide one-vote veto remains a formal capability-track design, **not** the current M930-3 demo display gate. It does not release `partial` objects, change document refusal, authorize numbers or close TS5. The current demo PDF-region display is a separate source view, not a second formal table-delivery path.
- Word export is not a prerequisite for the current interview demo; Markdown/Streamlit presentation comes first. A future change requires an explicit business decision first reflected in `AGENTS.md`, `DESIGN_V2.md`, and the roadmap; a lower-level Phase task cannot change this order by itself.

## 8. Current execution boundary

The active umbrella task remains `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`. The formal tree gate is still governed by `TREE_STRUCTURE_ADJUSTMENT_TASK.md`, while the current time-boxed execution lane is `2026-09-30_DEMO_BACKBONE_MILESTONE.md`.

At this point:

- R0 is closed; R1-A is frozen; R1-B is closed. Existing R2 code/results remain preserved as material-store, authority, trace, table-identity, and fallback-expansion work, but the old whole-Evidence/adjacent-block boundary model is superseded as the primary path.
- The user approved the tree-structure adjustment on 2026-09-16. TS0–TS4 are closed; the latest TS5 real run is fail-closed and TS5 remains open. Exact worktree/test status belongs only in `V2_TODO.md`.
- The user approved the 2026-09-30 Demo Backbone direction on 2026-09-20. M930-0 (read-only plan), M930-1 (scope/status artifacts), and M930-2 (representative real material and Pack slice) are complete and engineering-sealed; M930-2's earlier "eight qualified facts" content reading was corrected on 2026-09-21 — wire-adopted candidates are not qualified facts.
- The Demo lane is currently at **M930-3, which is in progress and not closed**. The documented successor includes formal `ExternalFact`; `ResearchMaterialDisposition`, `WriterMaterialProcessingDisposition`, and `FactNarrativeDisposition`; pre-gate `SectionDraft` subjects (`ClaimCandidate` and `NarrativeDraftUnit`) and proposals; exactly one aggregate binding decision per subject revision; factual-only entailment and context-only binding; one accepted binding per accepted proposal; later `SectionClaim`/final Narrative; and a post-gate `SectionResult` that references the Draft one way. Status correction (2026-09-27): these rules, the exact material context, `FollowUpNeed`, the four persistence boundaries and three identity sets now **have implementations in the workspace — uncommitted and with no real end-to-end chain re-verified**; a rule written down is not shipped code, and no real create-only run has exercised them. Three independent-gate P1 bypasses remain unfixed **on the current wire**; this is not a statement that the gates were repaired. M930-4/5 have not started.
- That file-level changelist and repository-wide impact inventory were produced and adjudicated; the subsequent user directives authorized the batch, which is what the workspace implementations above come from. **No new create-only real run, external retrieval or real LLM call is authorized by that history** — each still requires its own per-run authorization listed in a completed preflight.
- Business adjudication of 2026-09-28 (`DESIGN_V2.md` §0.17): the demo topic's tables are no longer acceptable as typed gaps alone. Before any change to TS5 / table-graph wiring code, a **minimal consistency revision** of `AGENTS.md`, `DESIGN_V2.md`, `V2_IMPLEMENTATION_PLAN.md`, `2026-09-30_DEMO_BACKBONE_MILESTONE.md`, `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` and the governing parent task books must land, stating the new business scope, exit conditions, and schedule impact. The batch's three work items are: (1) research dispatch and material acquisition; (2) the formal graph return path for tables, so the demo's target tables each obtain formal qualification and enter the Pack; (3) verifying that the Pack actually reaches the Writer. Historical TS5 no-go, trust roots and existing runs stay untouched; no staging, commit, or seal in this batch.
- Business sequencing adjudication of 2026-09-29 (`DESIGN_V2.md` §0.18–§0.20): the next Demo checkpoint selects only `company_business` plus financial `fin_source_scope`/`fin_solvency`, then exercises the same slice through independent review and read-only presentation; industry/external research follows afterward. Subject identity preflight remains mandatory, while unselected company/industry topics and external/event capabilities are explicitly out of scope, not passed. The original three-section scope and full M930 exit criteria remain historical/future comparison points. Target business tables use one graph→tool→Pack→Writer path with strict table-local complete proof (§0.19); unrelated document-wide gaps remain visible but do not veto an independently proven target table. The Pack-driven cited-writing path (§0.20) replaces the historical Claim-based writing path once implemented and verified. Neither design change asserts that code is wired, any table qualified, or any gate closed. Real LLM and external retrieval still require separate per-run approval.
- **2026-10-01 demo acceptance override (`DESIGN_V2.md` §0.21):** The §0.18–§0.19 graph→Pack target-table requirement above is retained as historical/formal capability work, not the next M930-3 demo prerequisite. Stage A now checks Pack→Writer→cited prose alongside a separate human-confirmed source-PDF-region display and independently qualified minimal business numeric facts; financial fact tables keep their own exact authority, display-tier and proxy-caliber checks. No old `partial/refused` table is relabeled; TS5, formal gates, M930-4/5, system release and human acceptance are not inferred. This change is not authorization for a new real LLM call, external retrieval or create-only run.
- Do not continue page/seed-specific R2 boundary patching, skip directly to report polishing, or rerun the full 41 questions before the tree gate passes.
- Outside the bounded M930 lane, do not start Phase 5 or Phase 6 product work while the P3R/P4R gate is open. M930 is not permission to alter frozen assets, weaken correctness gates, mark failed TS5 objects usable, or declare any downstream stage closed.

Common checks:

```bash
python -m scripts.demo_preflight
python -m evals.run_evals
streamlit run streamlit_app.py
```

Use module-specific CLI and focused eval commands from the active task before the full gate.

## 9. Documentation maintenance

- Product/architecture decisions change `DESIGN_V2.md` first, then roadmap, active task, and TODO.
- Historical reports retain their original facts and receive only a clear historical/superseded banner when needed.
- `CLAUDE.md` stays a short bootstrap; never copy this constitution into it.
- `README.md` describes what a user can actually run. Unverified fresh-install claims must be labeled.
- After doc changes, check authority/version links, stale status language, Markdown links, and whitespace. Documentation changes are committed separately from code.
