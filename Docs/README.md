# AEGIS AI — SIH 2026 Prototype

Sovereign On-Premise Agentic AI Workbench for confidential industrial work.

## What this project proves

The prototype demonstrates a local agentic workbench that can:

1. understand an employee's natural-language goal;
2. identify task intent and input modality;
3. select an appropriate workflow and capabilities;
4. automatically route model-dependent work through a provider-neutral model interface;
5. execute deterministic tools under a controlled Execution Controller;
6. observe execution results;
7. recover from bounded execution failures;
8. verify outputs;
9. generate real deliverables;
10. keep the workflow local and auditable.

The core design principle is:

> **LLM reasoning is probabilistic; execution must be deterministic and controlled.**

## Model-provider strategy

AEGIS uses a provider-neutral `ModelProvider` abstraction:

```text
AEGIS Agent → Model Router → ModelProvider
                         ├→ Local: Ollama / local endpoint
                         └→ Temporary API: development/testing only
```

The temporary API path is only for candidate-model evaluation when Colab/local serving is unavailable. Use synthetic or sanitized data only. OpenCode, if used during development for model access/testing, is not an AEGIS runtime dependency or a required coding agent.

## Demonstration workflows

### 1. Scanned inspection report → approval note

```text
Scanned PDF
→ PyMuPDF
→ Tesseract OCR
→ Agent interpretation
→ Optional local knowledge retrieval
→ Approval-note drafting
→ python-docx
→ Verification
→ Human approval
→ Final DOCX
```

### 2. Industrial statistics → computation → verified result

Example:

> From this month's equipment inspection readings, calculate the average measured thickness for each equipment item and identify which equipment has fallen below its minimum acceptable thickness.

```text
User request + Excel
→ Agent: computation + spreadsheet
→ Excel inspection
→ Agent determines computation
→ Coding Model
→ Python Sandbox
→ Observation
→ Agent correction if needed
→ Verification
→ Deliverable
```

### 3. Multimodal analysis

```text
Image
→ Vision capability
→ Local VLM
→ Agent interpretation
→ Result
```

## Architecture

```text
User
 ↓
Gradio UI
 ↓
Agent
 ↓
Execution Controller
 ↓
Capability Broker
 ├── Model Plane
 ├── Tool Plane
 └── Knowledge Plane
 ↓
Observation
 ↓
Agent
 ↓
Verify / Retry / HITL / Deliver
```

### Agent

Handles:
- intent,
- modality,
- planning,
- semantic reasoning,
- observation interpretation,
- next-action proposal,
- drafting.

### Execution Controller

Handles:
- authoritative state,
- workflow transitions,
- action validation,
- retries,
- iteration limits,
- verification gates,
- HITL,
- capability invocation,
- completion.

### Capability Broker

Resolves capability names to implementations.

### Model Router

Prototype routing is deterministic and explainable.

Example:
```text
general reasoning → Agent Model
code generation   → Coding Model
visual reasoning  → Vision Model
```

## Prototype stack

| Component | Technology |
|---|---|
| UI | Gradio |
| Backend | Python |
| Agent | Qwen3-8B, non-thinking |
| Orchestration | Custom lightweight loop |
| Controller | Custom deterministic state machine |
| Broker | Python |
| Router | Deterministic Python |
| Model interface | Provider-neutral `ModelProvider` |
| Local serving | Ollama / local OpenAI-compatible endpoint |
| Temporary testing | Generic API provider, synthetic/sanitized data only |
| OCR | Tesseract |
| PDF | PyMuPDF |
| Spreadsheet | openpyxl |
| Word | python-docx |
| PPT | python-pptx |
| Sandbox | Docker `--network none` |
| Sessions | SQLite |
| Audit | JSONL / SQLite |

## Repository rules

`ARCHITECTURE.md`
- Stable system and implementation decisions.
- Read before changing architecture or implementation.

`DEV_LOG.md`
- Living implementation state.
- Read before every implementation task.
- Update after every meaningful task.

`requirements.txt`
- Prototype Python dependencies.

## Development model

The code is developed using coding agents such as Codex, Cursor, and Antigravity.

The repository is the source of truth.

Google Colab is the GPU execution environment.

Google Drive is persistent supporting storage for datasets, demo files, artifacts, and model/cache data where appropriate.

```text
Codex / Cursor / Antigravity
            ↓
       Git repository
            ↓
         Colab
            ↓
       GPU execution
```

Do not make application logic depend on Colab.

## Implementation strategy

Build deterministic infrastructure first, then add the probabilistic Agent. The provider boundary is established before real model integration so model access can move from mocks → Colab/local serving → RTX 5050/Ollama without changing application logic.

```text
Phase 0  Foundation + provider interfaces
→ Phase 1  State + Controller
→ Phase 2  Broker
→ Phase 3  Registry + Router + ModelProvider
→ Phase 4  Mocks + tests
→ Phase 5  Real Agent
→ Phase 6  Computation vertical slice
→ Phase 7  Scanned-document vertical slice
→ Phase 8  Multimodal vertical slice
→ Phase 9  Sessions + UI
→ Phase 10 Audit + sovereignty evidence
→ Phase 11 Hardening + demo fixtures
→ Phase 12 Colab model validation
→ Phase 13 RTX 5050 deployment
```

The computation workflow is the first major end-to-end vertical slice because it exercises almost the complete architecture.

## Running

The exact setup commands will be added as the implementation stabilizes.

The intended environments are:

- development: laptop;
- GPU execution: Google Colab;
- later local deployment: RTX 5050 8GB system.

## Scope discipline

This is a prototype.

Do not add production infrastructure such as Kubernetes, enterprise identity, Qdrant, LangGraph, OPA, Langfuse, gVisor, Firecracker, or production model-serving infrastructure unless a specific later task requires it.

The production architecture remains technology-replaceable. The prototype's job is to prove the logical interfaces and core behavior.
