# AEGIS AI — Prototype Architecture

## 1. Purpose

This repository implements the prototype core of the SIH 2026 Sovereign On-Premise Agentic AI Workbench for confidential industrial work.

The prototype is deliberately scoped as a **working vertical slice of the finalized production logical architecture**. It must prove the core behaviors without prematurely implementing the full production stack.

The production architecture requires sovereignty, agentic execution, multi-model operation, capability extensibility, multimodal processing, local knowledge grounding, controlled execution, governance/HITL, and provable no-egress operation. The prototype implements the smallest credible slice of those requirements.

## 2. Architectural Invariants

These rules must not be broken casually.

### Sovereignty
- No confidential task data is sent to external AI services.
- Normal operation requires no cloud LLM or external API.
- Models, tools, knowledge, state, and artifacts remain local to the prototype environment.
- Network activity must be observable.
- Sandbox execution must have networking disabled.
- Any external API used during development/testing is outside the AEGIS runtime and may use only synthetic/sanitized data.

### Single-agent architecture
- There is one general-purpose Agent Runtime.
- Specialized models are capabilities/providers, not separate agents.
- The Agent does not directly execute arbitrary infrastructure operations.

### Agent vs Controller
**Agent = intelligence.**
- Understands goal.
- Determines intent and modality.
- Proposes plans.
- Determines required capabilities.
- Interprets observations.
- Decides whether to continue, correct, verify, or finish.
- Drafts semantic content.

**Execution Controller = deterministic execution governance.**
- Owns authoritative task state.
- Validates actions.
- Enforces workflow transitions.
- Enforces retry/iteration limits.
- Invokes capabilities.
- Enforces verification and HITL gates.
- Handles failures and completion.
- Emits audit/execution events.

The Agent proposes; the Controller governs.

### Capability abstraction
The Agent requests capabilities, not concrete libraries or infrastructure.

```text
Agent
  ↓
Capability Broker
  ↓
Model / Knowledge / Tool capability
  ↓
Observation
  ↓
Execution Controller
  ↓
Agent
```

### Model abstraction and routing
Model selection is a distinct concern. The Agent and workflows depend on a provider-neutral `ModelProvider` interface, not directly on Ollama, an API provider, or a specific SDK.

```text
AEGIS Agent / Capability
          ↓
     Model Router
          ↓
    ModelProvider
      ├── Local Provider
      │     └── Ollama / local OpenAI-compatible endpoint
      │
      └── API Provider
            └── development/testing only
```

For the prototype routing is deterministic and explainable:
- general reasoning/drafting → Agent Model
- code generation → Coding Model
- visual reasoning → Vision Model

The Model Router records the routing reason and selected model.

### Development/testing provider rule
A temporary external API/provider may be used to evaluate candidate models if Colab/local model serving is unavailable or impractical. This is a development/testing escape hatch, not part of the sovereign production architecture.

Rules:
- Never send confidential MRPL/enterprise data to an external provider.
- Use synthetic or sanitized fixtures only.
- Keep provider configuration outside business logic.
- Do not make OpenCode, or any particular provider, an AEGIS dependency.
- The same `ModelProvider` interface must support the later local Ollama deployment.

### Determinism
Prefer deterministic implementation for:
- file-type detection
- PDF extraction
- OCR
- spreadsheet parsing
- registries
- capability resolution
- routing
- workflow transitions
- retries/limits
- sandbox invocation
- artifact generation
- verification rules
- approval state
- audit events

Use LLMs for:
- semantic understanding
- planning
- interpretation
- drafting
- observation reasoning
- corrective decisions

## 3. Core Runtime

```text
USER
  ↓
Gradio UI
  ↓
Agent
  ↓
Execution Controller
  ↓
Capability Broker
  ├── Tool capabilities
  ├── Model capabilities
  └── Knowledge capabilities
  ↓
Observation
  ↓
Execution Controller
  ↓
Agent
  ↓
Verify / Retry / HITL / Deliver
```

The execution loop is:

```text
ACT → OBSERVE → REASON → ACT → OBSERVE → ...
```

The Controller is the authoritative state owner.

## 4. Workflows

### Workflow A — Scanned Inspection Report → Approval Note

User goal example:

> Prepare an approval note based on this inspection report.

```text
Request + scanned PDF
  ↓
Agent: intent=document_drafting
Agent: modality=scanned_document
  ↓
Inspection workflow
  ↓
PyMuPDF / file inspection
  ↓
Tesseract OCR
  ↓
Extracted text + confidence metadata
  ↓
Agent: identify relevant findings
  ↓
Optional local knowledge retrieval
  ↓
Agent: draft approval-note content
  ↓
python-docx
  ↓
Grounding / verification
  ↓
HUMAN APPROVAL
  ↓
FINAL DOCX
```

The document remains `DRAFT — pending approval` until approval.

### Workflow B — Industrial Data → Computation → Verified Deliverable

Representative use case:

> From this month's equipment inspection readings, calculate the average measured thickness for each equipment item and identify which equipment has fallen below its minimum acceptable thickness.

```text
Request + Excel
  ↓
Agent: intent=computation
Agent: modality=spreadsheet
  ↓
inspect_spreadsheet
  ↓
Structured workbook schema/data
  ↓
Agent determines computation
  ↓
generate_code
  ↓
Model Router
  ↓
Coding Model
  ↓
Python code
  ↓
Sandbox (network disabled)
  ↓
stdout / stderr / result
  ↓
Agent observes result
  ├── failure → diagnose → regenerate → sandbox
  └── success
  ↓
Verification
  ↓
Generate result/calculation deliverable
  ↓
DONE
```

The employee asks for a result, not code.

A sandbox failure should be used to demonstrate the agentic loop:

```text
ACT → OBSERVE ERROR → REASON → CORRECT → ACT
```

Retry and iteration counts are bounded by the Controller.

### Workflow C — Multimodal Analysis

Example:

> Inspect this equipment photograph and describe the visible condition that should be reviewed by the maintenance team.

```text
Image
  ↓
Agent: modality=image
  ↓
Vision capability
  ↓
Local VLM
  ↓
Visual observations
  ↓
Agent interpretation
  ↓
Result / report
```

Tesseract remains the ordinary scanned-document OCR path; a VLM is used for genuinely visual tasks.

## 5. Capability Vocabulary

Initial prototype capabilities:

```text
extract_document
inspect_spreadsheet
ocr_document
analyze_image
search_knowledge
draft_approval_note
generate_code
run_code
verify_result
generate_word
generate_excel
generate_ppt
finish
```

Capabilities are bounded and registered.

Unknown capabilities must fail gracefully rather than causing unsupported execution.

## 6. Controller State

The Controller owns a state similar to:

```text
TaskState
- session_id
- user_goal
- attachments
- intent
- modality
- selected_skill
- plan
- current_step
- completed_steps
- observations
- generated_artifacts
- verification_status
- retry_count
- iteration_count
- approval_status
- final_status
```

An Agent decision should be structured, not free-form execution instructions.

Conceptual shape:

```json
{
  "action": "generate_code",
  "inputs": {
    "computation": "...",
    "data_schema": "..."
  },
  "done": false
}
```

The Controller validates:
1. action is allowed in the current workflow;
2. prerequisites exist;
3. the step has not illegally repeated;
4. retry/iteration budget remains;
5. capability/policy permits the action.

## 7. UI

The prototype UI is a ChatGPT-like workbench built with Gradio.

Required interactions:
- New chat/session
- Session list/sidebar
- File attachment
- Natural-language task request
- Send/execute
- Streaming execution events
- Artifact display/retrieval
- Approval/rejection for workflows requiring HITL

Do not expose private chain-of-thought.

Show high-level execution events instead:

```text
Understanding request
Selected computation workflow
Inspecting workbook
Selected coding model
Generating calculation
Executing in sandbox
Verification passed
Preparing deliverable
```

## 8. Sessions

Each chat is an independent task context.

Persist at minimum:
- session ID
- messages
- uploaded file references
- workflow
- task state
- execution events
- artifacts
- approval status

Prototype persistence: SQLite.

## 9. Prototype Technology

| Area | Prototype |
|---|---|
| UI | Gradio |
| Backend | Python |
| Agent | Qwen3-8B, non-thinking mode |
| Agent loop | Custom lightweight Python loop |
| Controller | Custom deterministic state machine |
| Broker | Python module |
| Router | Deterministic Python router |
| Model interface | Provider-neutral `ModelProvider` |
| Local model provider | Ollama / local OpenAI-compatible endpoint |
| Temporary test provider | Generic API provider, development/testing only |
| OCR | Tesseract |
| PDF | PyMuPDF |
| Spreadsheet | openpyxl |
| Word | python-docx |
| PPT | python-pptx |
| Coding | Small local code-capable model |
| Sandbox | Docker, `--network none` |
| Session state | SQLite |
| Audit | JSONL and/or SQLite |
| Network visibility | Local network monitoring |

Avoid adding LangGraph, Qdrant, Keycloak, OPA, Langfuse, Kubernetes, gVisor, Firecracker, or other production infrastructure unless explicitly required by a later task.

## 10. Prototype-to-Production Boundary

The prototype keeps stable logical interfaces so implementations can later be upgraded.

```text
Prototype
→ Production direction

Ollama/local serving
→ vLLM / SGLang-class serving

Rule router
→ gateway / semantic / learned routing

Custom loop
→ durable orchestration such as LangGraph

Lightweight local retrieval
→ Qdrant-class knowledge service

Docker network isolation
→ gVisor / Firecracker-class isolation

SQLite / JSON audit
→ production tracing/audit platform

SQLite metadata
→ PostgreSQL

YAML/JSON registry
→ schema-validated registry service

Prototype network controls
→ formal air-gapped segmentation + egress enforcement
```

The interfaces should remain stable even when implementations change.

## 11. Development Rule

Every implementation task must preserve:
- the architecture,
- bounded execution,
- local-first operation,
- capability abstraction,
- provider/model abstraction,
- controller ownership of state,
- and testability.

Before modifying code, coding agents must read:
1. `ARCHITECTURE.md`
2. `DEV_LOG.md`
3. relevant source files
4. relevant tests

After modifying code, they must:
1. run relevant tests/checks;
2. report changed files;
3. update `DEV_LOG.md`;
4. record blockers and the next concrete task.

## 12. Development Environment

Coding agents are Codex, Cursor, and Antigravity. They modify the Git repository but are not part of the AEGIS runtime.

Google Colab Pro is the interim GPU environment. The later local target is an RTX 5050 8GB system.

A provider-neutral model layer allows this progression without changing Agent/Controller/Broker/workflow logic:

```text
Mock Provider → Colab/local Provider → RTX 5050 + Ollama
```

A temporary external API/provider may be used only for model evaluation with synthetic/sanitized data if local serving is blocked.

Source of truth:
```text
Git repository
```

Persistent supporting storage:
```text
Google Drive
- demo inputs
- model/cache data where appropriate
- artifacts
- backups
```

Execution:
```text
Colab runtime
```

The application must not contain Colab-specific business logic. Environment-specific configuration belongs in configuration files/environment variables.

## 13. Implementation Priority

Build in these phases:

```text
Phase 0  Foundation + provider-neutral interfaces
Phase 1  State + deterministic Controller
Phase 2  Capability Broker + capability registry
Phase 3  Model Registry + Router + ModelProvider abstraction
Phase 4  Mock providers/capabilities + integration tests
Phase 5  Real Agent integration
Phase 6  Computation vertical slice
Phase 7  Scanned-document vertical slice
Phase 8  Multimodal vertical slice
Phase 9  Sessions + Gradio UI
Phase 10 Audit + network/sandbox evidence
Phase 11 End-to-end hardening + demo fixtures
Phase 12 Colab model validation
Phase 13 RTX 5050 local deployment
```

The computation workflow remains the first major end-to-end vertical slice because it exercises Agent reasoning, file inspection, capability resolution, model routing, code generation, sandbox execution, observation, correction, verification, and deliverable generation.

## 14. Success Definition

The prototype is successful when one local session can demonstrate:

- automatic model/capability selection across at least two task types;
- scanned inspection report → Word approval note;
- business/engineering computation request → generated code → sandbox execution → verification → deliverable;
- multimodal input → local visual analysis;
- visible ACT/OBSERVE/REASON-style execution events;
- bounded agentic recovery from execution failure;
- human approval for the approval-note workflow;
- visible evidence of zero external application calls during the sovereign/local demo.

The temporary API/provider route is only for model evaluation with synthetic/sanitized data and is not part of this success criterion.

The prototype should be presented as the implemented core slice of the finalized production architecture, not as the complete production deployment.
