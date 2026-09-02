# DEV_LOG

This file is the living engineering memory for the AEGIS AI prototype.

## How to use this file

Every coding agent (Codex, Cursor, Antigravity, etc.) must read this file before implementation work.

After each meaningful implementation task, update this file with:
- what changed,
- files changed,
- tests/checks performed,
- current status,
- blockers,
- next concrete task.

`ARCHITECTURE.md` contains stable architectural decisions.
`DEV_LOG.md` contains evolving implementation state.

---

# 2026-09-02 — Project Baseline

## Objective

Build a working prototype of the SIH 2026 Sovereign On-Premise Agentic AI Workbench.

The prototype is a deliberately scoped core slice of the finalized production logical architecture.

## Stable Decisions

### Architecture

- Single general-purpose Agent Runtime.
- Execution Controller is separate from the Agent.
- Agent proposes actions; Controller governs execution.
- Capability Broker abstracts concrete capabilities.
- Model Router is a distinct component.
- Prototype routing is deterministic and explainable.
- Tools/models are registered capabilities.
- Controller owns authoritative task state.
- Execution is bounded by retry and iteration limits.
- Agent can observe execution results and decide whether to continue/correct/verify.
- Specialized coding/vision models are not separate agents.
- Model access is behind a provider-neutral `ModelProvider` interface.

### Agent

- Model target: Qwen3-8B.
- Use non-thinking mode.
- Agent responsibilities: goal understanding, intent, modality, planning, semantic interpretation, observation reasoning, next-action decision, drafting.
- Agent must return structured decisions for execution.

### Model access / provider abstraction

- Local model execution is the normal path.
- Ollama/local OpenAI-compatible serving is the initial local target.
- A generic API provider may be used temporarily for candidate-model evaluation if Colab/local serving is unavailable or inconvenient.
- The temporary API path is development/testing only and uses synthetic/sanitized data.
- OpenCode is not an AEGIS runtime dependency or required coding agent.
- Later RTX 5050 deployment must switch providers/runtime without changing Agent/Controller/Broker/workflow logic.

### OCR / Documents

- OCR: Tesseract.
- PDF extraction: PyMuPDF.
- Ordinary scanned-document workflow uses deterministic OCR first.
- VLM is reserved for genuinely visual/multimodal tasks.

### Computation Workflow

The employee asks for a business/engineering result, not for code.

Representative task:
"From this month's equipment inspection readings, calculate the average measured thickness for each equipment item and identify which equipment has fallen below its minimum acceptable thickness."

Flow:
```text
User request + file
→ Agent determines intent/modality
→ inspect file
→ Agent determines required computation
→ generate_code capability
→ Model Router
→ Coding Model
→ Sandbox
→ observation
→ Agent decides continue/correct
→ verification
→ deliverable
```

### UI

- Gradio.
- ChatGPT-like interaction.
- Sidebar with multiple sessions.
- File attachment.
- Natural-language request.
- Streaming high-level execution events.
- Deliverable retrieval.
- HITL approval where required.
- Do not display chain-of-thought.

### Storage

- SQLite for prototype session/state metadata.
- JSONL and/or SQLite for audit.
- Google Drive is supporting storage, not the source of truth.
- Git repository is source of truth.

### Development Environment

- Laptop has no GPU.
- GPU-dependent execution uses Google Colab Pro.
- Future target deployment: RTX 5050 8GB laptop.
- Model-provider configuration must remain portable across mock, Colab/local, and Ollama deployments.
- Code must not hard-code Colab-specific behavior.

### Sandbox

- Docker with `--network none` for prototype where the runtime supports it.
- Generated code must never execute directly on the host.
- Capture stdout/stderr and execution status.

## Current Repository State

### Completed

- [x] Architectural decisions consolidated
- [x] Prototype functional/technical design drafted
- [x] Initial four-file documentation plan defined
- [x] Create implementation repository structure (`aegis` packages)
- [x] Add Python project configuration (`pyproject.toml`, root `requirements.txt`)
- [x] Add tests (skeleton import checks; configuration loading/validation checks)
- [x] Add configuration loading
- [x] Define provider-neutral shared schemas/interfaces
- [x] Define provider-neutral `ModelProvider` generation interface
- [x] Define deterministic prototype workflow transitions
- [x] Implement deterministic Execution Controller and Broker boundary
- [x] Define common capability interface and configured registry
- [x] Implement registry-backed Capability Broker resolution/invocation

### In progress

- [x] Implement provider-neutral TaskState schema (typed statuses, records, limits)
- [x] Implement Execution Controller
- [x] Implement registry-backed Capability Broker resolution/invocation
- [ ] Implement Model Registry/Router (beyond validated external configuration)

### Not started

- [ ] Local Agent integration
- [ ] Computation workflow
- [ ] Coding model integration
- [ ] Sandbox integration
- [ ] Tesseract workflow
- [ ] Word generation
- [ ] Multimodal workflow
- [ ] Session UI
- [ ] Audit UI/events
- [ ] Network evidence
- [ ] End-to-end demo hardening
- [ ] RTX 5050 deployment

## Engineering Rules

1. Do not redesign the architecture while implementing a small task.
2. Do not add production infrastructure prematurely.
3. Prefer interfaces and deterministic components.
4. Use mocks to test controller/broker/router before requiring GPU models.
5. Do not expose chain-of-thought in the UI.
6. Do not let the Agent execute arbitrary tools directly.
7. Do not bypass the Capability Broker.
8. Do not bypass the Controller for execution.
9. Every meaningful execution must produce an observation.
10. Every code execution must be sandboxed.
11. Every workflow with a required approval gate must remain draft until approval.
12. Unknown capability requests must fail gracefully.
13. Keep model/provider configuration externalized.
14. Keep implementation portable between Colab and RTX 5050 local deployment.
15. Never use confidential data with the temporary external API/testing provider.
16. Do not make OpenCode or another provider a runtime dependency.
17. Keep the ModelProvider interface small and interchangeable.

## Agent Handoff Protocol

When switching from Codex → Cursor → Antigravity or vice versa:

1. Read `ARCHITECTURE.md`.
2. Read this file.
3. Inspect current code and tests.
4. Continue from the current `Next Task`.
5. Do not assume previous conversational context.
6. Update this file before finishing.

## Revised Phase Plan

### Phase 0 — Foundation + Provider-Neutral Interfaces
1. Create repository structure.
2. Add configuration loading.
3. Define common interfaces/schemas.
4. Define `ModelProvider`.
5. Add pytest setup.

### Phase 1 — State + Controller
1. Implement `TaskState`.
2. Implement workflow/state definitions.
3. Implement deterministic Controller.
4. Implement bounded retries/iterations.
5. Test Controller.

### Phase 2 — Capability Broker
1. Define capability interface.
2. Implement registry.
3. Implement Broker resolution.
4. Add mock capabilities.
5. Test Broker.

### Phase 3 — Model Registry + Router
1. Implement model registry/config schema.
2. Implement deterministic Router.
3. Implement routing decision/audit structure.
4. Add availability/fallback handling.
5. Test Router.

### Phase 4 — Provider Layer + Integration Tests
1. Define local provider adapter shape.
2. Define generic API provider adapter shape without binding to OpenCode.
3. Implement `MockModelProvider`.
4. Test provider calls.
5. Verify provider swapping leaves Controller/Broker/workflows unchanged.

### Phase 5 — Real Agent
1. Structured intent/modality.
2. Structured plan proposal.
3. Observation-reasoning decision.
4. Drafting.
5. Integrate only through Router → ModelProvider.
6. Regression tests.

### Phase 6 — Computation Vertical Slice
1. Spreadsheet inspection.
2. Computation skill.
3. Coding Model through Router/Provider.
4. Docker sandbox.
5. Capture stdout/stderr/exit status.
6. Bounded Agent correction.
7. Deterministic verification.
8. Calculation deliverable.
9. Synthetic end-to-end fixture.

### Phase 7 — Scanned Document Vertical Slice
1. PyMuPDF extraction.
2. Scanned-page detection.
3. Tesseract OCR.
4. Inspection-note skill.
5. Approval-note drafting.
6. DOCX generation.
7. Grounding/verification.
8. HITL approval.
9. End-to-end fixture.

### Phase 8 — Multimodal Vertical Slice
1. Vision capability.
2. Local VLM through Router/Provider.
3. Multimodal skill.
4. Representative-image test.

### Phase 9 — Sessions + UI
1. Session manager.
2. Gradio shell.
3. Upload/task submission.
4. Streaming high-level events.
5. Artifact retrieval.
6. Approval controls.
7. UI integration tests.

### Phase 10 — Audit + Sovereignty Evidence
1. Structured audit logger.
2. Routing/provider/tool/verification/approval events.
3. Network monitor/evidence.
4. Docker network-isolation proof.
5. Visible sovereignty evidence.

### Phase 11 — End-to-End Hardening
1. Failure matrix.
2. Graceful degradation.
3. Regression tests.
4. Deterministic demo fixtures.
5. Repeated final demo path.
6. Confirm no external calls during sovereign demo.

### Phase 12 — Colab Model Validation
1. Validate Agent model.
2. Validate Coding Model.
3. Validate Vision Model.
4. Measure memory/latency.
5. Freeze demo model configuration.
6. Validate all workflows locally.

### Phase 13 — RTX 5050 Local Deployment
1. Configure local provider/runtime.
2. Install Ollama/local serving.
3. Load selected models.
4. Use sequential model loading where VRAM requires it.
5. Run workflow tests locally.
6. Run offline/no-egress demonstration.
7. Confirm application logic is unchanged.

## Next Task

**Phase 3.1 — Implement deterministic model registry access and Router selection.**

The next coding agent must read `ARCHITECTURE.md`, `DEV_LOG.md`, the external model configuration, `ModelProvider` interface, capability metadata, and existing tests; implement deterministic model-role selection and explainable routing without concrete model connectivity; retain mock-only provider implementations; preserve Controller/Broker boundaries; update this file; and stop after this task.

