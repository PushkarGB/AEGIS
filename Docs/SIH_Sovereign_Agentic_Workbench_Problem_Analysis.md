# Sovereign On-Premise Agentic AI Workbench for MRPL — Problem & Requirements Analysis

**Purpose of this document:** to establish, before any architecture or tech-stack decision, a reliable answer to *"what exactly must this workbench be capable of doing?"* It does not propose a design, pick a model stack, or define a prototype. Every claim is marked as either a **fact** (extracted from the problem statement or verified via web research, with a source) or an **inference** (a reasonable industry-based deduction that has not been independently confirmed for MRPL specifically).

---

## 1. Problem Definition

### 1.1 Core Problem (Fact — extracted from problem statement)
MRPL and comparable PSU/defence-linked industrial units generate large volumes of routine but confidential knowledge work — approval notes, board material, engineering calculations, internal tool code, and review of scanned drawings/inspection reports. This work cannot use cloud AI assistants (Claude, Codex, ChatGPT, etc.) because company policy keeps confidential data (P&IDs, financials, vendor negotiations, unreleased designs, internal correspondence, business strategy) on-premises. The stated consequence is a fork in behaviour: staff either forgo AI assistance and lose productivity, or they informally paste confidential material into public tools, creating an uncontrolled data-leak risk. No deployable product today gives industrial users something that works the way Claude or Codex works, while staying fully on-premises.

### 1.2 Why It Exists (Fact + Inference)
- **Fact:** Company/government data-handling policy prohibits sending this class of data to external/cloud services.
- **Inference (industry pattern, not MRPL-specific):** This policy almost certainly derives from a combination of (a) India's critical-infrastructure protection regime — refineries fall under the kind of critical information infrastructure that the National Critical Information Infrastructure Protection Centre (NCIIPC) is chartered to safeguard under Section 70A of the IT Act — and (b) data-localization pressure building under the DPDP Act 2023, which is pushing entities toward keeping AI inference and storage inside Indian/organizational boundaries.
- **Fact (industry-wide, not MRPL-confirmed):** Frontier AI capability has only recently become available in genuinely open-weight form (Apache-2.0/MIT-class models such as GPT-OSS-120B/20B, Qwen3, Llama, DeepSeek) that can run entirely offline on a single high-VRAM GPU, which is why the problem statement frames this as newly "realistic" rather than something that should have existed years ago.
- **Inference:** The "shadow IT" behaviour described (employees quietly using public tools) is a known and documented risk pattern across regulated industries generally; it is plausible at MRPL but not something we can verify has actually occurred there.

### 1.3 Target Users / Stakeholders (Fact + Inference)
| Stakeholder | Role implied by the problem statement | Status |
|---|---|---|
| Engineers (process, mechanical, instrumentation) | Calculations, drawing review, technical documentation | Fact |
| Inspection/maintenance personnel | Scanned inspection reports, handwritten notes, photographs of equipment | Fact |
| Approving authorities / managers | Review and sign off on approval notes drafted by the assistant | Inference (a human-approval step is implied but not spelled out as a named role) |
| Internal IT/software developers | Code for internal tools, review, sandboxed execution | Fact |
| Corporate/finance/strategy staff | Board presentations, vendor negotiation material, confidential business strategy documents | Fact |
| Document/records/knowledge management custodians | Own the SOPs, manuals, past correspondence that must be grounded via the knowledge base connector | Inference |
| IT/OT security and compliance function | Must validate and continuously audit the "no external calls" claim | Inference, strongly implied by the demonstration requirement |
| SIH evaluators/judges | Require a demonstrable, verifiable prototype | Fact |

### 1.4 Explicit Requirements (Fact — directly stated in the problem text)
1. Self-hosted, air-gapped deployment on the organization's own GPU server; nothing leaves the premises.
2. Backend must support **multiple** open-weight models simultaneously, not be locked to one model.
3. **Automatic model selection** per task (a coding request handled differently from a document-summary request).
4. New open-weight models must be addable later **without redesigning the system**.
5. The assistant must act **agentically**: plan multi-step work, call local tools (file read/write, sandboxed code execution, spreadsheet work, internal document search), and iterate rather than answer once and stop.
6. Must handle **more than text**: scanned PDFs, handwritten notes, engineering drawings, photographs — via on-device OCR and vision models.
7. Output must be **real deliverables**: approval notes, PPT/Word/Excel files, working code, calculations with steps shown — not just chat replies.
8. Must ground itself in the organization's own manuals, SOPs, and past correspondence via a **local knowledge base connector**, again without external calls.
9. Demonstrable on a single workstation/server with a mid-range GPU (with an explicit fallback to a smaller open-weight model if 120B-class hardware is unavailable at the venue — a direct reference to models like GPT-OSS-120B, which needs roughly a single 80GB-class GPU).
10. Demonstration must show model auto-selection across **at least two task types**.
11. Demonstration must carry an **agentic task end-to-end** (scanned inspection report → key findings → drafted Word approval note).
12. Demonstration must include a **coding task run and verified in a sandbox**.
13. Demonstration must include a **multimodal task** (image or scanned-document understanding).
14. Demonstration must show, via logs or a visible network monitor, that **no external calls are made at any point** — this is described as the actual proof of the sovereignty claim.

### 1.5 Strongly Implied Requirements (Inference, but well-grounded in the text)
- **Human-in-the-loop approval** for anything that becomes an official document (approval notes, board material) — the problem statement talks about "drafting" an approval note, not auto-issuing one, implying a human must review/approve before it counts as an organizational record.
- **Traceability/auditability** of what the agent read, generated, and whom it should be attributed to — implied by "approval note" being a governance artifact and by the explicit requirement to prove no external calls (an audit-logging capability that would naturally extend to full action logging).
- **Extensibility of tools**, not just models — the text lists file I/O, code execution, spreadsheet work, and document search as tool categories, implying a tool/plugin architecture rather than a fixed toolset.
- **Multi-user or at least multi-task-type operation** on shared GPU hardware, since a "workbench" implies routine, repeated use by many staff across different departments — this is not explicit but is implied by calling it an organizational "workbench" rather than a personal assistant.
- **Version- and update-manageability without internet access** — if new open-weight models must be addable "later," and the network is air-gapped, there must be some controlled-import mechanism (implied, not stated).
- **Graceful degradation on smaller hardware** — the explicit fallback clause implies the architecture must not hard-depend on a specific model size class.

### 1.6 Constraints (Fact, drawn directly from the text, organized by category)
| Category | Constraint |
|---|---|
| Security | No external network calls at any point; confidential data (P&IDs, financials, vendor negotiations, designs, correspondence) must never leave the premises |
| Deployment | Self-hosted; demonstrable on a single workstation or server |
| Hardware | Mid-range GPU target; 120B-class model as an aspiration, smaller open-weight model as an explicit fallback |
| Networking | Air-gapped; the system must be able to *prove* isolation via logs or a visible network monitor |
| Data | Uses only open-source models and publicly available sample data for the SIH demo (sample scanned PDFs, open-dataset P&IDs) — no proprietary MRPL data will actually be used in the competition itself |
| Operational | Model roster must be extensible without redesign; tools must be local (sandboxed code execution, file I/O, spreadsheet, document search) |

### 1.7 Explicit Success Criteria (Fact)
A working local deployment that demonstrates, in a single session:
1. Automatic model routing/selection across at least two different task types.
2. One agentic task carried through end-to-end producing a real deliverable (Word approval note from a scanned inspection report).
3. One coding task executed and verified in a sandbox.
4. One multimodal task involving image or scanned-document understanding.
5. Visible proof (logs/network monitor) of zero external network calls throughout.

### 1.8 Important Ambiguities / Open Questions (these are genuinely unresolved by the text)
- **How many models, and how different?** "Multiple open-weight models" could mean two models of very different sizes/modalities, or a larger heterogeneous fleet (e.g., a reasoning LLM + a vision-language model + a small fast classifier). The routing granularity (per-conversation vs per-step vs per-sub-task) is not specified.
- **What counts as "auto-selection"?** A simple rule ("if the file extension is .py, use the code model") already satisfies the letter of the requirement; the problem statement does not say whether a learned/semantic router is expected, and judges may weight this differently.
- **Where does the OCR/vision pipeline end and the LLM begin?** It's unclear whether "on-device OCR and vision models" means a classical OCR engine (e.g., Tesseract/PaddleOCR) feeding a text LLM, or a single vision-language model doing OCR+reasoning end-to-end, or both.
- **Depth of "agentic"** — is a two-step plan (read report → draft note) sufficient, or does the jury expect iterative self-correction, tool retries, and multi-turn planning?
- **What exactly must the knowledge-base connector index**, and how is it kept current without internet access (manual ingestion? scheduled offline sync? one-time corpus load for the demo)?
- **Multi-user vs single-user scope for the prototype** — the "workbench" framing suggests eventual multi-user deployment, but the demo only needs to run on one workstation; concurrency is not tested explicitly.
- **What "verified in a sandbox" means for the coding task** — automated test execution, output inspection by a human, or the agent self-checking its own output are all plausible readings.
- **Governance of agent-generated official documents** — the text never states who signs off, what retention/audit trail is required, or how a wrong/hallucinated approval note is caught before it does harm; this is left entirely open.
- **Scale of the "later" model-addition requirement** — whether this must be demonstrated in the prototype (e.g., visibly registering a new model at runtime) or is only an architectural aspiration for judges to infer from the design.

---

## 2. MRPL / Industrial Context

### 2.1 Verified Facts About MRPL
MRPL (Mangalore Refinery and Petrochemicals Limited) is a Schedule 'A' Miniratna Central Public Sector Enterprise, a subsidiary of ONGC, operating under the Ministry of Petroleum & Natural Gas and is categorized as a Category 1 Schedule 'A' Miniratna CPSE. It was established in 1988 and now has a refining capacity of approximately 15 million metric tonnes per annum, with advanced units such as hydrocrackers, continuous catalytic reformers, and a polypropylene plant. A distinguishing technical fact is that MRPL is the only Indian refinery equipped with two hydrocrackers, used to produce premium diesel. It operates an aromatic/petrochemical complex producing para-xylene and benzene, two captive jetties at New Mangalore Port, a single point mooring facility, and rail/truck loading infrastructure for petroleum coke ([MarketScreener business profile](https://www.marketscreener.com/quote/stock/MANGALORE-REFINERY-AND-PE-6493081/company/)). As of the most recent public filing referenced, the company reported roughly 2,530 employees company-wide (same source) and quarterly revenue in the tens of thousands of crores of rupees, indicating a large, financially and operationally complex enterprise with correspondingly large volumes of routine paperwork.

No public information was found describing MRPL's internal AI, digital-transformation, or IT-security programs in enough detail to characterize its current tooling; general web searches on "MRPL digital transformation" returned only generic industrial-cybersecurity content, not MRPL-specific material. **This is treated as an information gap, not a fact about MRPL's technology posture** — the team should not assume MRPL has (or lacks) any particular existing digital system.

### 2.2 Typical Confidential Industrial Knowledge Work (Inference, industry-pattern based)
The problem statement names several document types directly (approval notes, board presentations, engineering calculations, internal tool code, scanned drawings, inspection reports). Beyond those, refining-industry sources describe the broader digital-refinery landscape:
- Refiners increasingly deploy AI/ML for predictive maintenance, advanced process control, and digital twins — HPCL, for instance, has publicly stated it deploys AI, advanced process control, and digital twins to improve predictive maintenance, reduce downtime, optimize energy use, and reduce flaring.
- Industry commentary frames the "digital refinery" concept as integrating AI to optimize maintenance, supply chains, quality control, and compliance, with real-time analytics increasingly central to refinery decision-making.
- A concrete Indian example: HPCL's Mumbai refinery deployed AI-based soft sensors (built on Aspen tools) that predict product-quality parameters in real time, reducing reliance on manual lab sampling.
These are **general industry patterns, not confirmed MRPL practices** — but they indicate the kind of adjacent, data-heavy workflows (quality control, maintenance scheduling, process optimization) that plausibly generate the "routine but sensitive" work the problem statement references, beyond the four examples it names explicitly.

### 2.3 Workflow Categories (Inference, structured from the problem statement + industry norms)
| Category | Plausible activities (industry-pattern inference unless marked Fact) |
|---|---|
| Engineering | Process/mechanical calculations, line/pump/vessel sizing notes, deviation analysis (Fact: "engineering calculations" named explicitly) |
| Maintenance | Inspection scheduling, root-cause write-ups, spare-parts correspondence |
| Inspection | Reading scanned inspection reports, extracting findings, flagging anomalies (Fact: named explicitly as the flagship demo use case) |
| Documentation | SOP drafting/updates, technical manuals, correspondence archives (Fact: SOPs and past correspondence named explicitly as RAG sources) |
| Approval | Drafting and routing approval notes for sign-off (Fact: named explicitly) |
| Analytics | Summarizing operational/financial data, trend reporting (Inference) |
| Coding | Internal tool development and maintenance (Fact: "code for internal tools" named explicitly) |
| Knowledge management | Search across manuals, SOPs, and correspondence (Fact: named explicitly as the "local knowledge base connector") |

### 2.4 Types of Sensitive Documents/Data Involved (Fact, from the problem statement, organized)
- Piping & Instrumentation Diagrams (P&IDs)
- Financial data
- Vendor negotiation records
- Unreleased engineering designs
- Internal correspondence
- Confidential business strategy documents
- Scanned inspection reports, handwritten notes, photographs of plant/equipment

### 2.5 Realistic Opportunities for an On-Premise Agentic System (Inference, but tightly scoped to what the text supports)
- Converting the backlog of scanned/paper inspection and maintenance records into searchable, structured findings without ever uploading them anywhere.
- Reducing the manual burden of turning raw findings (inspection notes, meeting minutes, calculation sheets) into the polished-document deliverables (approval notes, board decks, calculation reports) that the organization already produces by hand.
- Making the existing corpus of SOPs and past correspondence queryable in natural language, on-site, functioning as an institutional-memory search layer.
- Assisting with internal tool code (scripts, dashboards, automation) without any code or credentials leaving the network — this is a distinct workflow from the document/vision work and needs its own tool (a sandboxed interpreter), which the problem statement calls for explicitly.
- Reducing the "shadow IT" incentive by making an on-prem alternative fast and capable enough that staff no longer feel compelled to paste confidential material into public tools.

### 2.6 Industrial and Regulatory Constraints Relevant to This Class of Deployment (Fact, general regulatory context — not MRPL-confirmed policy)
- **Critical infrastructure protection:** The National Critical Information Infrastructure Protection Centre (NCIIPC), created under Section 70A of the IT Act (amended 2008) and operating under the National Technical Research Organisation, is India's nodal agency for protecting critical information infrastructure such as energy systems. A refinery of MRPL's scale plausibly falls within or adjacent to this regime, though MRPL's specific CII designation was not independently confirmed in this research.
- **Data localization pressure:** India's DPDP Act framework is moving toward requiring that AI models and associated data processing stay within Indian infrastructure, which would affect how any AI system — cloud or on-prem — is expected to be architected for compliance.
- **OT/SCADA-specific caution:** Broader industrial-cybersecurity literature stresses that SCADA/industrial-control systems historically relied on air-gapped isolation for security, and that digital-transformation initiatives have been progressively eroding that isolation by connecting OT to IT networks and the internet. This is a generic industry risk, not an MRPL-specific fact, but it explains why a genuinely air-gapped AI workbench (rather than one that "phones home" for updates, licensing, or telemetry) is treated as a hard requirement rather than a nice-to-have: a deployment that claims to be on-premise but still calls out for license validation or telemetry is not truly air-gapped — it is what one industry source calls a "compliance fiction."
- **Safety/auditability norms in refining:** Refineries operate under strict HSE (health, safety, environment) and change-management regimes; any AI-generated document that could influence a safety- or compliance-relevant decision (an approval note, an inspection finding) would be expected, by general industry norm, to require human sign-off before being treated as an official record. This is an inference, not a stated MRPL policy, but is consistent with how the problem statement frames the AI's output as a *draft* to be reviewed rather than an autonomous decision.
- **Legacy systems:** Problem statement and general refinery-digitalization literature both point to the presence of legacy, paper-based, and scanned-document workflows still running alongside any newer digital systems, which is precisely why OCR/vision capability is treated as core rather than optional.

---

## 3. Existing Solutions & Gap

This section catalogs what already exists in each relevant technology layer, compares real capabilities and limitations, and identifies what a competition-grade sovereign workbench actually needs to add. No final stack is chosen here.

### 3.1 On-Premise / Air-Gapped AI Platforms
Air-gapped LLM deployment is now an established product category, not a research idea. Industry guidance frames air-gapped deployment as the strictest form of on-premise AI — no internet egress, controlled-transfer model/update delivery, and telemetry that never phones home — and notes that the hard part is not the model itself (open-weight LLMs run fine offline) but building an orchestration, RAG, and governance layer that functions with zero cloud dependency. Concretely:
- **onprem.ai** (Switzerland) packages a hardened Linux + Kubernetes + GitOps stack with vLLM/SGLang/llama.cpp/TensorRT-LLM underneath, sold as "plug & play" sovereign infrastructure ([onprem.ai](https://www.onprem.ai/en/platform/)).
- **Rexon Cyber** and similar consultancies offer bespoke air-gapped LLM deployment services layering NIST/CIS/zero-trust controls over open models like Llama/Mistral/Falcon ([Rexon Cyber](https://www.rexoncyber.com/air-gapped-llm/)).
- **AirgapAI (Iternal Technologies + Intel)** has been demonstrated for U.S. military use, processing an 11-million-word dataset in two hours while functioning entirely offline.
- **Gap for this problem:** these are general-purpose private-AI platforms. None of them ship agentic tool orchestration, document-generation, or industrial-drawing understanding out of the box — they solve the "keep the model on-prem" layer, not the "act like Claude/Codex for engineering paperwork" layer that the problem statement actually asks for. The reusable part is the *infrastructure pattern* (hardened host, containerized inference, no-egress network policy); the *application layer* (agent, RAG, document output, OCR) has to be built.

### 3.2 Open-Weight LLM Serving Engines
There is a mature, well-documented set of serving engines, and the right choice differs by workload rather than by a single "best" answer:
- **Ollama / LM Studio** — experience-layer tools built on llama.cpp (and increasingly MLX on Apple hardware), ideal for single-user or small-team local development because of near-zero setup friction, but without continuous batching, so throughput collapses under concurrent load.
- **vLLM / SGLang** — multi-user serving engines built around continuous batching and (for vLLM) PagedAttention memory management; vLLM has the broadest hardware support, SGLang leads on prefix-heavy/agentic workloads via RadixAttention. Benchmarks cited across sources show roughly an order-of-magnitude-plus throughput advantage for vLLM over Ollama under concurrent load (a widely cited figure is 793 tokens/sec for vLLM versus 41 tokens/sec for Ollama on the same hardware at peak concurrency), though at batch size one the two are within roughly 20% of each other, so the gap only matters once multiple users or agent loops hit the server simultaneously.
- **TensorRT-LLM + Triton** — production-scale, NVIDIA-only, highest throughput ceiling, heaviest operational complexity.
- **Gap:** none of these engines natively solve "run several different models side-by-side and pick between them automatically" — that is a separate routing layer (Section 3.3). They solve *how* one model is served efficiently, not *which* model handles a given request.

### 3.3 Multi-Model Routing
This is directly relevant to the "automatically pick the right model for a task" requirement, and the landscape is more mature than might be expected:
- **RouteLLM** (academic/open-source) demonstrated that a learned router can send only a small fraction of queries to an expensive/strong model while retaining 95% of a frontier model's quality and cutting cost by roughly 85%, by sending only about 14% of queries to the expensive model.
- **vLLM Semantic Router** — an open project that performs signal-driven routing across models directly inside the vLLM serving layer, including built-in categories for reasoning-vs-non-reasoning decisions and safety classification.
- **LiteLLM / Portkey / NVIDIA LLM Router** — general-purpose proxy/gateway routers; LiteLLM is an open-source, self-hostable Python SDK/proxy that calls 100+ LLMs through a unified OpenAI-compatible format with fallbacks, load balancing, and budget controls, while NVIDIA's LLM Router is designed to return a model recommendation rather than proxy the call itself, leaving retries/fallback/logging to the calling application.
- **Routing strategies in general** fall into three families — rule-based, semantic (embedding similarity to known task clusters), and predictive/learned (a model that scores query–model fit and optimizes for a cost/quality trade-off).
- **Gap:** almost all published routing systems are optimized for a **cost/quality trade-off between models of similar modality** (e.g., "send hard queries to a bigger text model"). None of the reviewed systems are built around routing across **modalities and tool needs** — i.e., "this is a scanned drawing, route to the vision model; this is a Python script, route to the code model and open a sandbox tool." That specific routing problem (task-type classification driving both model *and* tool selection) is closer to what this SIH problem needs, and is not a solved, off-the-shelf capability.

### 3.4 Agentic AI Frameworks
The field has consolidated quickly. As of 2026, the credible general-purpose options are:
- **LangGraph** — positioned as the default choice for stateful production workflows, particularly in regulated environments where auditability, deterministic control, and human-approval steps matter — directly relevant given this problem's approval-note and human-sign-off implications.
- **CrewAI** — the fastest path from idea to a working multi-agent prototype, using role-based "crews," though teams often outgrow its simpler orchestration model at scale.
- **Microsoft Agent Framework** — the April 2026 unification of Semantic Kernel and AutoGen into one SDK, best suited to .NET/Azure-native enterprises.
- **OpenHands (formerly OpenDevin), SWE-Agent, Devin-style agents** — standalone autonomous environments focused specifically on resolving software-engineering tasks end-to-end, distinct from the general orchestration frameworks above.
- **Claude Agent SDK** and OpenAI Agents SDK — vendor-native agent SDKs; relevant as *design references* for tool-calling and sandboxing patterns, but not usable here since the workbench must run entirely on open-weight models, not proprietary APIs.
- **Gap:** these frameworks solve orchestration (state, tool-calling loops, multi-agent handoff) but assume you already have a capable underlying model and already-built tools (file I/O, sandbox, spreadsheet, retrieval). None of them ship an OCR/vision pipeline, a document-generation pipeline, or an air-gap-verification/audit layer — those remain to be assembled specifically for this problem.

### 3.5 Local RAG / Enterprise Knowledge-Management Systems
This layer directly serves the "local knowledge base connector" requirement:
- **Frameworks:** LlamaIndex, LangChain, and Haystack are the standard retrieval-orchestration frameworks, typically paired with a self-hosted vector index (Qdrant, Weaviate, or Milvus) and a local embedding/reranking model served via vLLM, Ollama, or Text Embeddings Inference.
- **Vector databases:** Qdrant is generally the lighter, simpler default for most production RAG pipelines, while Milvus is preferred for billion-scale or distributed deployments.
- **Full-stack open-source RAG platforms:** RAGFlow (Apache 2.0, 80,000+ GitHub stars) specializes in deep document parsing — tables, scanned PDFs, slides — and has recently added sandboxed code execution and agent memory, making it one of the more complete open building blocks for this kind of workbench. Onyx (MIT license) has been demonstrated fully air-gapped on local GPUs at a university scale (37,000+ users), showing this class of tool is proven at real air-gapped scale, not just theoretical.
- **Gap:** these platforms are built for *document/text* RAG. None of the reviewed products natively index **engineering drawings** (P&IDs) as a first-class, structured retrieval object (as opposed to just an image blob) — that requires the drawing-digitization capability discussed next, feeding a specialized index rather than being solved by generic document RAG.

### 3.6 Multimodal Document, Drawing, and Handwriting Understanding
This is the layer that determines whether "scanned inspection report → approval note" and "P&ID understanding" are actually achievable on open weights.
- **General vision-language OCR/document models:** Qwen3-VL supports OCR across 32 languages, is robust to low light, blur, and tilt, handles rare characters and jargon, and has improved long-document structure parsing, and earlier generations already set state-of-the-art results on composite OCR/document benchmarks such as OmniDocBench and CC-OCR, outperforming strong competitors including InternVL2.5-78B. Independent testing found that a mid-sized Qwen model (around 9B parameters) hit a practical "sweet spot" for OCR quality versus resource cost when run locally.
- **Document-parsing-specific tools:** MinerU and similar open document-parsing pipelines specialize in converting scanned/PDF documents (including tables and multi-column layouts) into structured, machine-readable text.
- **P&ID-specific digitization:** this is a narrower, harder problem than generic OCR, and there is real prior art:
 - Symbol-detection pipelines (e.g., RF-DETR-based approaches) can reach around 99% mAP detecting P&ID symbol classes and, combined with targeted OCR, extract instrument tags — but such outputs are explicitly positioned as an engineering aid, not an automated safety or compliance decision system.
 - TCS Research's "Digitize-PID" is a notable Indian-origin solution: an end-to-end pipeline detecting pipes, symbols, and text in P&IDs, associating them, and validating/correcting results using domain knowledge — directly relevant given MRPL's own Indian-PSU context.
 - Comparable pipelines exist from AWS (Bedrock Data Automation + SageMaker) and Azure (AutoML-trained symbol detectors), both cloud-based and therefore **not directly reusable** in an air-gapped deployment without re-hosting the underlying models locally.
- **Gap:** generic vision-language models are strong at reading text and describing images, but *specialized* P&ID symbol/graph extraction (turning a drawing into a structured symbol-and-connectivity graph, not just a description) is still a narrower research-grade capability with no single, drop-in, fully open-source, production-grade tool. For a competition demo, a general VLM doing OCR + qualitative description of a sample drawing is realistic; true engineering-grade P&ID digitization (the TCS/AWS/Azure-style symbol graph) is a stretch goal, not a baseline expectation.

### 3.7 Coding Agents and Sandboxed Execution
- **OpenHands / SWE-Agent** are the leading open frameworks purpose-built for agents that write, run, and debug code autonomously inside a sandbox, iterating on failures. These differ from general orchestration frameworks like LangGraph/CrewAI/AutoGen in that they provide a standalone, ready-made autonomous coding environment rather than a generic multi-agent graph.
- Local model coding capability is now credible: local coding agents running against Ollama/LM Studio-hosted open models have shown large relative speedups on coding-agent benchmarks with recent local inference-engine updates, and require no per-call API cost.
- **Gap:** the "sandbox" in these frameworks is typically a container/VM for *running arbitrary code the agent writes*, which is a well-solved, common pattern (Docker or similar isolation, no network egress). The specific requirement here is less about novel sandbox technology and more about **plugging a sandbox tool into the same agent loop that also does document/RAG work** — i.e., integration, not invention.

### 3.8 Enterprise Document-Generation Systems
The problem statement requires *real deliverables* (Word/PPT/Excel, not chat text). This is a comparatively mature, low-risk layer:
- Standard open libraries (python-docx, python-pptx, openpyxl-class tools) are the common mechanism by which agents actually produce Office-format files; several of the RAG/agent platforms above (e.g., RAGFlow's 2026 release) have begun bundling document-generation and sandboxed execution as native features rather than leaving it to bespoke glue code.
- **Gap:** this layer is the least novel part of the whole system — the risk here is not capability but **quality and correctness of the generated content** (i.e., making sure a drafted approval note faithfully reflects the source inspection report), which loops back to the RAG/grounding and human-approval requirements rather than to the file-generation mechanics themselves.

### 3.9 Sovereign / Private AI Platforms — Broader and Indian Context
- Globally, Gartner's Predicts 2026 report on AI sovereignty projects that by 2030 more than 75% of European and Middle Eastern enterprises will repatriate AI workloads for geopolitical-risk reasons, up from under 5% today — evidence that this problem class (sovereign, on-prem AI for regulated industry) is a recognized and growing global category, not a niche concern.
- In India specifically, the **IndiaAI Mission** has moved from funding compute to shipping actual sovereign foundation models: Sarvam AI was selected in April 2025 to build a sovereign large language model in the roughly 120-billion-parameter range using a meaningful share of Indian-language training data, and by February 2026 Sarvam had open-sourced Sarvam-30B and Sarvam-105B, the first Indian foundation models trained end-to-end on IndiaAI Mission compute, alongside BharatGen, Krutrim, and others. These are relevant as *available open-weight options with Indian-language strength* but are general-purpose chat/reasoning models — by the developers' own account they are not yet close to frontier reasoning models, and none of them ship industrial/engineering-domain tuning, vision capability for drawings, or agentic tooling specific to this problem.
- **Gap:** the Indian sovereign-AI ecosystem currently supplies *candidate base models* (a resource for the "which open-weight model(s) to run" decision, to be made later), not a ready-made industrial agentic workbench. Nothing found in this research is a packaged product that combines air-gapped multi-model serving + agentic tool use + multimodal document/drawing understanding + Office-document generation + auditable network isolation in one deployable unit for an industrial user. That combination — not any single component — is the actual gap this SIH problem is asking teams to fill.

### 3.10 Summary: Reusable vs. Genuinely Missing
| Layer | Maturity of existing solutions | Reusable as-is? |
|---|---|---|
| Air-gapped hosting pattern (no-egress network, hardened host) | Mature, documented, several vendors | Yes — pattern is well understood |
| Open-weight model serving (single model) | Very mature (vLLM, Ollama, etc.) | Yes |
| Multi-model routing by cost/quality | Mature for same-modality text routing | Partially — needs extension to modality/tool-aware routing |
| Agent orchestration (planning, tool loops) | Mature frameworks exist | Yes, as a library — application logic still needed |
| Generic document RAG | Mature, several production-grade options | Yes |
| Generic OCR / scanned-document understanding | Mature (Qwen-VL family, MinerU-class tools) | Yes |
| Engineering-drawing (P&ID) structured digitization | Research-grade / narrow production tools (TCS, AWS, Azure) | Partially — good for a demo, not turnkey production-grade in open source |
| Sandboxed code execution for agents | Mature | Yes |
| Office-document generation | Mature, low-risk | Yes |
| **End-to-end integration of all of the above, air-gapped, for an industrial user, with auditable isolation proof** | **Not found as an existing product** | **No — this is the actual deliverable** |

---

## 4. Complete Use-Case Universe

Use cases are grouped into **Mandatory** (directly named or unambiguously required by the SIH text) and **High-Value Implied** (well-justified by the background/description but not literally spelled out as demo requirements). Each row uses the requested structure.

### 4.1 Mandatory / Core Use Cases

**UC-1: Scanned Inspection Report → Draft Approval Note (flagship end-to-end agentic task)**
- **User Goal:** Turn a scanned inspection report into a reviewable, formatted approval note without retyping or manually re-reading the whole document.
- **Input:** Scanned PDF/image of an inspection report (possibly containing tables, stamps, handwriting).
- **Required AI Capabilities:** OCR/vision-language document understanding; summarization; key-findings extraction; structured drafting in an organizational note format; multi-step planning (read → extract → draft → format).
- **Required Tools:** Vision/OCR model, file read/write, Word-document generation tool, (optionally) RAG lookup against an approval-note template or past examples.
- **Knowledge/RAG Need:** Medium — benefits from a template/style corpus of past approval notes, not strictly required for a minimal demo.
- **Multimodal Need:** High (this is the multimodal + agentic proof point at once).
- **Expected Output:** A formatted .docx approval note draft with extracted key findings and a recommendation section.
- **Human Approval Need:** Mandatory — output is explicitly a *draft* for a human approver, not an auto-issued document.
- **Security Considerations:** Source document may contain equipment IDs, safety findings, or plant-specific detail; must never leave the local environment at any pipeline stage (OCR, LLM, storage).

**UC-2: Coding Task Run and Verified in a Sandbox**
- **User Goal:** Get a working, tested piece of code (script, internal tool fix, data-processing utility) without exposing source or credentials externally.
- **Input:** Natural-language coding request, optionally with an existing internal script to modify.
- **Required AI Capabilities:** Code generation/completion; self-verification (running tests, checking output); iterative debugging.
- **Required Tools:** Sandboxed code-execution environment (isolated, no network egress), file read/write.
- **Knowledge/RAG Need:** Low for a demo; higher in production if grounding on an internal codebase/style guide.
- **Multimodal Need:** None.
- **Expected Output:** Working code plus an execution/verification log (test results, sample output).
- **Human Approval Need:** Recommended before deployment into any production internal tool, though not strictly required to "view" the sandboxed result.
- **Security Considerations:** Sandbox must be fully isolated (no network, restricted filesystem) to prevent code from becoming an exfiltration vector; execution logs may need to be retained for audit.

**UC-3: Multimodal Image/Drawing Understanding (P&ID or equipment photograph)**
- **User Goal:** Ask questions about, or extract information from, a technical drawing or a photograph of plant equipment.
- **Input:** Image file (photograph, scanned drawing, P&ID excerpt from an open dataset for the demo).
- **Required AI Capabilities:** Vision-language understanding; optionally symbol/OCR extraction for drawings; natural-language Q&A grounded in the image.
- **Required Tools:** Vision-language model; optionally a specialized symbol-detection component for P&IDs.
- **Knowledge/RAG Need:** Low for a basic Q&A demo; higher if cross-referencing drawing content against SOPs or equipment manuals.
- **Multimodal Need:** High (this is the dedicated multimodal proof point).
- **Expected Output:** Natural-language answers, extracted labels/tags, or a structured summary of the drawing/image content.
- **Human Approval Need:** Required before any extracted reading is used for a safety- or compliance-relevant decision; not required for exploratory Q&A.
- **Security Considerations:** Drawings are named explicitly as confidential (P&IDs); for the competition, only open-dataset sample drawings are used, but the architecture must behave identically for a real confidential drawing.

**UC-4: Model Auto-Selection Across Task Types**
- **User Goal:** (System-level, not really a single end-user goal) — the workbench should transparently route a document-summary request to one model and a coding request to another, without the user manually choosing.
- **Input:** Any user request; the router inspects task type/modality.
- **Required AI Capabilities:** Task classification (rule-based, semantic, or learned); model-capability awareness.
- **Required Tools:** A routing/gateway layer sitting in front of the model pool.
- **Knowledge/RAG Need:** None directly, though routing quality can be improved by logging past routing outcomes.
- **Multimodal Need:** Indirect — the router itself must recognize modality (text vs. image vs. code) to route correctly.
- **Expected Output:** Correct model invoked per request; ideally a visible log/trace showing which model handled which task and why.
- **Human Approval Need:** None for routing itself.
- **Security Considerations:** Routing metadata/logs should not leak content of the underlying request beyond what's needed for audit.

**UC-5: Air-Gap / No-External-Call Verification**
- **User Goal:** (System-level) — prove, not just claim, that no data left the premises during any of the above tasks.
- **Input:** N/A — this is an observability requirement layered over all other use cases.
- **Required AI Capabilities:** None directly (this is infrastructure, not model capability).
- **Required Tools:** Network monitor/logging tool, or an equivalent audit-log mechanism, visible during the demo.
- **Knowledge/RAG Need:** None.
- **Multimodal Need:** None.
- **Expected Output:** A visible log or live network-traffic view showing zero outbound calls across the whole demo session.
- **Human Approval Need:** None — this is evaluator-facing evidence, not a workflow output.
- **Security Considerations:** This *is* the security consideration for the entire system; every other use case's compliance with "no external calls" is validated here.

### 4.2 High-Value Implied Use Cases

**UC-6: Approval Note / Board Presentation Drafting from Meeting Inputs**
- **User Goal:** Turn raw meeting notes, data points, or a rough outline into a polished PPT or approval-note draft.
- **Input:** Text notes, bullet outlines, or a data table.
- **Required AI Capabilities:** Structured drafting, summarization, narrative generation from data.
- **Required Tools:** PPT/Word generation tool.
- **Knowledge/RAG Need:** Medium (organizational template/style consistency).
- **Multimodal Need:** Low (unless source notes are handwritten/scanned, in which case it overlaps with UC-1's OCR path).
- **Expected Output:** Draft .pptx or .docx file.
- **Human Approval Need:** Mandatory before circulation, given board/decision-making sensitivity.
- **Security Considerations:** Board material and strategy content are explicitly named as confidential; identical on-prem handling required.

**UC-7: Engineering Calculation with Steps Shown**
- **User Goal:** Get a calculation (e.g., a sizing or process check) performed with a transparent, auditable derivation, not just a final number.
- **Input:** Parameters (e.g., flow rate, pressure, dimensions) provided in text or extracted from a document.
- **Required AI Capabilities:** Numerical reasoning; step-by-step derivation with unit-consistency; ideally cross-checked via actual code execution rather than pure LLM arithmetic.
- **Required Tools:** Sandboxed code/calculation execution (reuses UC-2's sandbox), file output.
- **Knowledge/RAG Need:** Medium — may need reference standards or prior calculation templates from the internal knowledge base.
- **Multimodal Need:** Low, unless input parameters come from a scanned data sheet.
- **Expected Output:** A calculation note/document showing inputs, formulae, intermediate steps, and the final result.
- **Human Approval Need:** Mandatory — engineering calculations affecting plant operation require qualified sign-off.
- **Security Considerations:** Calculation inputs may reveal proprietary process parameters; output must stay local.

**UC-8: Internal Document / SOP / Correspondence Search (Knowledge-Base Q&A)**
- **User Goal:** Ask a natural-language question and get an answer grounded in the organization's own manuals, SOPs, and past correspondence.
- **Input:** Natural-language question.
- **Required AI Capabilities:** Retrieval-augmented generation; citation of source document/section; refusal to answer outside the retrieved context (to avoid hallucination on policy-sensitive content).
- **Required Tools:** Local vector/document index, retrieval pipeline, LLM.
- **Knowledge/RAG Need:** High — this use case *is* the knowledge-base connector requirement.
- **Multimodal Need:** Medium, if SOPs/correspondence include scanned or image content (ties back to OCR pipeline).
- **Expected Output:** A natural-language answer with source citations to the underlying SOP/manual/correspondence.
- **Human Approval Need:** Not required for informational queries; required if the answer feeds into a decision or document.
- **Security Considerations:** Access control matters here more than anywhere else — not all staff should be able to retrieve all correspondence; role-based access to the knowledge base is a strongly implied need even though the problem statement doesn't mention it explicitly.

**UC-9: Vendor Correspondence / Negotiation Material Review or Drafting**
- **User Goal:** Get help drafting or reviewing a vendor communication or negotiation summary.
- **Input:** Draft correspondence, contract excerpts, or negotiation notes.
- **Required AI Capabilities:** Drafting, tone/consistency checking, summarization of key commercial terms.
- **Required Tools:** Document generation, optionally RAG against past vendor correspondence for consistency.
- **Knowledge/RAG Need:** Medium-to-high.
- **Multimodal Need:** Low, unless the source is scanned.
- **Expected Output:** Draft correspondence or a negotiation-position summary document.
- **Human Approval Need:** Mandatory — vendor negotiations are explicitly named as highly confidential and commercially sensitive.
- **Security Considerations:** Among the most sensitive categories named in the problem statement; strict on-prem handling and probably restricted user access.

**UC-10: Spreadsheet Analysis and Reconciliation**
- **User Goal:** Get help analyzing, cleaning, or reconciling tabular/financial or operational data.
- **Input:** Existing spreadsheet(s) or extracted tabular data (e.g., from a scanned data sheet).
- **Required AI Capabilities:** Data analysis, formula generation, anomaly/reconciliation checks.
- **Required Tools:** Spreadsheet-manipulation tool (reads/writes Excel), sandboxed code execution for computation.
- **Knowledge/RAG Need:** Low-to-medium.
- **Multimodal Need:** Low, unless sourced from a scanned document.
- **Expected Output:** Updated/annotated Excel file, or a new analysis workbook.
- **Human Approval Need:** Recommended before the output is used for a financial or operational decision.
- **Security Considerations:** Financial data is explicitly named as confidential; same on-prem handling applies.

**UC-11: Handwritten Note / Shift-Log Transcription**
- **User Goal:** Convert handwritten operator notes, shift logs, or field annotations into searchable digital text.
- **Input:** Photograph or scan of handwritten material.
- **Required AI Capabilities:** Handwriting-capable OCR/vision-language recognition; correction/normalization of recognized text.
- **Required Tools:** Vision/OCR model, file write, optionally an index-ingestion step feeding UC-8's knowledge base.
- **Knowledge/RAG Need:** Low for transcription itself; the *output* often feeds the knowledge base (UC-8).
- **Multimodal Need:** High — this is one of the harder OCR sub-cases (handwriting varies far more than printed text) and was named explicitly in the background text.
- **Expected Output:** Digitized, searchable text (and optionally a structured log entry).
- **Human Approval Need:** Recommended spot-checking given handwriting-OCR error rates, especially before the transcript is treated as an official record.
- **Security Considerations:** Same as other scanned-document handling — local-only processing.

**UC-12: Multi-Step Investigation / Cross-Document Compliance Check**
- **User Goal:** Have the agent pull together information from multiple internal sources (an incident report, the relevant SOP, and past similar incidents) to support an investigation or compliance review.
- **Input:** A triggering document (e.g., an incident report) plus an implicit need to cross-reference other stored material.
- **Required AI Capabilities:** Multi-step planning; multi-document retrieval and synthesis; comparison/gap-analysis reasoning.
- **Required Tools:** RAG/document search, file read/write, document generation for the final summary.
- **Knowledge/RAG Need:** High — this is the deepest agentic + RAG combination in the use-case set.
- **Multimodal Need:** Medium (source incident reports may be scanned).
- **Expected Output:** A synthesized findings/gap-analysis document.
- **Human Approval Need:** Mandatory — compliance and safety implications require human review of any AI-assembled cross-reference before action is taken.
- **Security Considerations:** Combines several sensitive-document categories at once; access control and audit trail matter most here, since the agent is autonomously deciding what to retrieve and combine.

> **Scope discipline note:** use cases such as "predictive maintenance analytics," "digital twin simulation," or "IoT sensor anomaly detection" were deliberately **excluded**, even though they appear frequently in general refinery-AI literature (Section 3.9's HPCL/BP examples). They are not supported by anything in the problem statement (which is about document/knowledge work, not real-time process control) and would inflate scope beyond what SIH is actually asking for.

---

## Final Synthesis

### A. Core Problem, in Brief
- MRPL and similar PSU/defence-linked industrial units cannot use cloud AI assistants for a large share of their daily knowledge work because the underlying data is confidential (P&IDs, financials, vendor negotiations, unreleased designs, correspondence, strategy).
- Company policy mandates the data stay on-premises, and open-weight models have only recently become capable enough to make an on-prem alternative realistic.
- Without such an alternative, staff either lose productivity by working manually, or informally leak confidential data into public AI tools.
- The requested solution is not a single model but a **workbench**: multiple open-weight models, automatically routed by task, operating agentically (planning, tool use, iteration), across both text and multimodal inputs (scanned documents, drawings, photos), producing real office-format deliverables, grounded in the organization's own SOPs/correspondence, and provably air-gapped.
- The demonstration bar is specific and achievable: model auto-selection across ≥2 task types; one full agentic document task; one verified sandboxed coding task; one multimodal task; and visible proof of zero external network calls.
- Every individual technical layer needed for this (serving, routing, agent orchestration, RAG, OCR/vision, sandboxing, document generation) already exists in the open-source ecosystem; what does not exist as a product is the **integrated, air-gapped, auditable combination of all of them aimed at an industrial user**.

### B. Top System Capabilities Required (not yet an architecture — a capability checklist)
1. Multi-model hosting with a task-aware routing layer that can be extended with new models without redesign.
2. Agentic orchestration: multi-step planning, tool invocation, iteration/self-correction.
3. A tool set covering at minimum: file I/O, sandboxed code execution, spreadsheet manipulation, document (Word/PPT/Excel) generation, and internal document/knowledge-base search.
4. Multimodal input handling: OCR for printed and handwritten text, vision-language understanding for photographs and drawings.
5. Local RAG grounded in organizational SOPs/manuals/correspondence, ideally with source citation and access control.
6. A verifiable no-external-call guarantee, observable via logs or a live network monitor.
7. A human-approval checkpoint for any output that becomes (or feeds) an official document or safety/compliance-relevant decision.
8. Basic audit logging of what was read, generated, and by which model/tool, sufficient to support the sovereignty/trust claim beyond a single demo session.

### C. Complete, Prioritized Use-Case List
**Mandatory (must be demonstrably working):**
1. UC-1 — Scanned inspection report → draft approval note (flagship agentic + multimodal task)
2. UC-2 — Coding task run and verified in a sandbox
3. UC-3 — Multimodal image/drawing understanding
4. UC-4 — Model auto-selection across task types
5. UC-5 — Air-gap / no-external-call verification

**High-value implied (strengthen the submission, in descending order of alignment with the stated background):**
6. UC-8 — Internal SOP/correspondence knowledge-base Q&A (directly named in the text as a required connector)
7. UC-11 — Handwritten note/shift-log transcription (directly named as an input type)
8. UC-6 — Approval note/board presentation drafting from meeting inputs (directly named deliverable types)
9. UC-7 — Engineering calculation with steps shown (directly named deliverable type)
10. UC-10 — Spreadsheet analysis and reconciliation (directly named deliverable type)
11. UC-9 — Vendor correspondence review/drafting (directly named sensitive-data category)
12. UC-12 — Multi-step cross-document investigation/compliance check (most advanced agentic behavior; good stretch goal, higher implementation risk)

### D. Critical Gaps Existing Solutions Do Not Adequately Address
1. **No single open-source product integrates** air-gapped multi-model serving + task-aware routing + agentic tool orchestration + multimodal document/drawing understanding + Office-document generation + auditable network isolation. Each layer is solved individually; the integration is not.
2. **Modality/tool-aware routing is immature.** Existing routers (RouteLLM-style) optimize cost/quality trade-offs within one modality; none of the reviewed systems route based on "this needs vision" vs. "this needs a code sandbox" vs. "this needs RAG" as a first-class decision.
3. **Engineering-drawing (P&ID) digitization is not turnkey.** Good research-grade and narrow production tools exist (TCS Digitize-PID, AWS/Azure pipelines, RF-DETR symbol detectors), but none is a drop-in, fully open-source, high-accuracy component ready for an air-gapped deployment without adaptation.
4. **No reviewed platform ships a built-in "prove sovereignty" mechanism** — i.e., a first-class, demo-ready network-isolation monitor/audit trail. Air-gapping is treated as an infrastructure/network-policy concern handled outside the AI application, not as an integrated, user-visible feature of the platform itself.
5. **Governance/human-approval workflow is generally absent** from the open agentic frameworks surveyed. LangGraph supports human-in-the-loop as a pattern, but no reviewed tool provides an out-of-the-box "draft → route to approver → sign-off → finalize" workflow specific to documents like approval notes.
6. **Indian sovereign foundation models (Sarvam, BharatGen, Krutrim) are general-purpose**, not tuned for engineering/industrial document work or vision tasks, and are not bundled with any of the agentic/RAG/document tooling this problem needs — they are a candidate model source, not a solution.

### E. Questions That Must Be Resolved Before Architecture Design
1. How many models, of what types (reasoning/text, code, vision-language, possibly a fast classifier for routing), should the prototype actually host given a mid-range-GPU constraint, and what does "smaller open-weight model" fallback mean concretely if 120B-class hardware isn't available?
2. What routing mechanism (rule-based, semantic, or learned) is expected or will be most convincing to evaluators, given that a simple rule-based router already satisfies the literal requirement?
3. How deep must "agentic" behavior go — is a fixed two/three-step pipeline acceptable, or must the system show genuine iterative planning and self-correction?
4. What is the minimum viable definition of "verified in a sandbox" for the coding use case — automated tests, output inspection, or agent self-verification?
5. What does the knowledge-base connector need to index for the demo (a small curated sample corpus vs. a larger simulated SOP archive), and how is it updated in an air-gapped setting?
6. Is engineering-grade P&ID symbol/graph digitization in scope, or is general vision-language OCR/description of a sample drawing sufficient for the multimodal proof point?
7. What form should the "no external calls" proof take — a live network monitor during the demo, exported logs, or both — and what level of technical verification will judges expect?
8. Who or what role plays the "human approver" in the demo, and how explicitly must the human-in-the-loop step be shown versus simply asserted in the design?
9. Is any access-control/multi-user model expected for the prototype, or is a single-user, single-session demo sufficient given the "workstation" framing?
10. How should the team position the P&ID/OCR gap (Section D.3) honestly in the submission — as a solved capability, a partially solved one with cited prior art, or an explicitly acknowledged limitation with a roadmap?

---

*This report is a problem-and-requirements analysis only. It intentionally does not select a final model roster, serving engine, agent framework, or system architecture — those decisions should be made in a subsequent design phase informed by the capabilities and gaps identified above.*
