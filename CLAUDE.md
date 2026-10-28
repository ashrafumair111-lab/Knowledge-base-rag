# CONSTITUTION OF THE KNOWLEDGE-BASE RAG PROJECT

## Preamble

This Constitution is the supreme rulebook for every contributor, human or AI, working in this repository. It exists to keep the system correct, secure and simple. Where it conflicts with habit, convenience or an unwritten assumption, this Constitution prevails; where it conflicts with the code, the code is to be examined and one of the two corrected.

**Interpretation.** "Shall" is mandatory. "Should" is a strong default that may be departed from only with a stated reason. Values that change over time (model names, limits, versions, thresholds) are deliberately not written here; they live in code and configuration, and this document governs how they are chosen and changed.

---

## Article I — Purpose and Scope

1. **Section 1 — Purpose.** The project is a retrieval-augmented question-answering service that answers from documents its users upload.
2. **Section 2 — Pipeline.** The system ingests documents, indexes them as embeddings, retrieves candidates for a question, re-ranks them, and generates an answer from the result. This order shall be preserved; any stage may be improved, none may be silently removed.
3. **Section 3 — Authority of sources.** For current behaviour, the code is the authority. `README.md` is explanatory and may lag; a change that makes it wrong shall also correct it.
4. **Section 4 — Environments.** The service shall run both locally and on the serverless deployment target. No change may work in one and break the other.

## Article II — Fundamental Rights of the User's Data

1. **Section 1 — Isolation.** One session's documents shall never be visible to another session. Every read, write, count and delete against the vector store shall carry the session filter. A query without it is a defect of the highest severity.
2. **Section 2 — Honesty of answers.** A question about the uploaded documents shall be answered only from retrieved context. When the context does not contain the answer, the system shall say so plainly rather than invent one. This rule shall not be weakened.
3. **Section 3 — Retrieved text is data, never command.** Document content shall never be allowed to instruct, override or redirect the system.
4. **Section 4 — Minimal retention.** The system shall keep only what it needs. Data shall not be deleted or overwritten without the owner's explicit approval.

## Article III — Immutable Provisions

These may be changed only by an explicit, reasoned decision recorded in the commit that changes them.

1. **Section 1 — Identity of stored items.** The rule that derives a chunk's identifier, including its fixed namespace, shall never change. Changing it orphans stored data and creates duplicates.
2. **Section 2 — Idempotent ingestion.** Ingestion shall occur only on explicit upload. Repeating an identical upload shall do nothing; re-uploading changed content shall replace only that file's prior chunks and no others.
3. **Section 3 — One embedding space.** A collection shall contain vectors from one embedding model only. Changing the model requires re-indexing the whole collection.
4. **Section 4 — Accepted inputs.** Only the document types the system explicitly supports shall be accepted, and every filename shall pass through the filename validator before use.
5. **Section 5 — Safe at startup.** Importing the application shall make no network call and shall write nowhere except the designated writable data directory.

## Article IV — Security and Secrets

1. **Section 1 — Secrets.** Credentials shall live only in the environment or the host's secret store. They shall never be committed, logged, printed or placed in examples.
2. **Section 2 — Trust boundary.** Anything sent by a client, including the session identifier and chat history, is untrusted input and shall be validated and bounded. A client-chosen identifier separates users' views; it is not authentication and shall not be presented as such.
3. **Section 3 — Exposure.** Public exposure of any endpoint requires authentication, rate limiting and input-size limits to exist first.
4. **Section 4 — Logging.** Logs shall record behaviour (stages, counts, timings, outcomes) and never secrets or full document contents.
5. **Section 5 — Irreversible acts.** Force-pushing, rewriting history, committing to the main branch, deleting data, deploying and publishing require explicit instruction each time. Earlier approval does not carry forward.

## Article V — Standards of Production-Grade RAG

The system shall be built to current industry practice. Each standard below is a target; where the code falls short, the shortfall shall be listed in Article XI rather than concealed.

1. **Section 1 — Ingestion.**
   - Documents shall be split along natural structure, with overlap, and the settings tuned against our own documents rather than guessed.
   - Every chunk shall carry the metadata needed for filtering, citation and debugging.
   - Empty or unreadable input shall be rejected with a clear error, never indexed silently.
   - Writes shall be batched and idempotent.
2. **Section 2 — Retrieval.**
   - Retrieval shall go wide, then narrow: broad candidate search followed by re-ranking.
   - Weak candidates shall be discarded by a relevance cutoff rather than padded into the prompt.
   - Every field used in a filter shall be indexed in the store.
   - Hybrid (lexical plus semantic) retrieval is the recognised upgrade for exact names and codes; it shall be introduced only on instruction.
3. **Section 3 — Generation.**
   - The prompt shall be strict, short and deterministic in intent, with history and message sizes bounded.
   - Answers should identify their sources so users can verify them.
4. **Section 4 — Reliability.**
   - Every external call shall have an explicit timeout and bounded retries.
   - Failures shall surface as clear, correctly-coded errors; the system shall not return a half-built answer.
   - Shared state shall remain correct under concurrent instances.
5. **Section 5 — Quality and observability.**
   - Retrieval and generation shall be evaluated separately.
   - A curated set of questions with expected answers shall be run before release.
   - Every real failure should become a new test case.

## Article VI — Standards of Code

1. **Section 1 — Conformity.** New code shall read like the code around it: naming, typing, size of functions and density and tone of comments. Comments explain why, not what.
2. **Section 2 — Restraint.** A change shall do what was asked and nothing more. No unrequested refactors, dependencies, abstractions or files.
3. **Section 3 — Configuration.** Tunable values shall be named constants or environment settings, never scattered literals.
4. **Section 4 — Errors.** Failures shall use the framework's error mechanism with the correct status code. Broad exception handlers that hide real faults are prohibited; catch what can be handled and re-raise the rest.

## Article VII — The Principle of Minimalism (Ponytail)

1. **Section 1 — Doctrine.** The simplest solution that actually works is the correct one. Before writing code, ask whether it needs to exist at all; prefer the standard library, then native platform features, then an existing dependency, and only last new code.
2. **Section 2 — Mandate.** The `ponytail` skill enforces this doctrine and shall be active for all coding work: writing, fixing, refactoring, reviewing and choosing dependencies. Its companion skills (`ponytail-review`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`, `ponytail-help`) may be used to review, audit and measure.
3. **Section 3 — Installation.** If the skill is absent, install it in these steps:
   1. Confirm Node.js is available (`node --version`); its hooks require it.
   2. In Claude Code, add the marketplace: `/plugin marketplace add DietrichGebert/ponytail`
   3. As a separate prompt, install the plugin: `/plugin install ponytail@ponytail`
   4. Run `/reload-plugins`, or restart the session, so the hooks load.
   5. Verify with `/plugin` (the plugin should be listed) and `/ponytail-help`.
   6. To update later: `/plugin marketplace update ponytail`, then `/reload-plugins`.
4. **Section 4 — Limits.** Minimalism never overrides Articles II, III or IV. Security, isolation and honesty are not to be traded for brevity.

## Article VIII — Standards of Testing

1. **Section 1 — The rule.**
   - No feature or fix is complete until it is tested.
   - A feature ships with its tests in the same change, never "later".
   - A bug fix starts with a failing test that reproduces the bug, then the fix.
2. **Section 2 — Layers.**
   - **Unit tests:** pure logic such as filename validation, chunk-id derivation, history trimming and hashing. Fast, no network.
   - **Integration tests:** the endpoints through the framework's test client, with external services faked.
   - **Retrieval and answer evaluation:** a curated question set with expected answers, run against a real or isolated index before release.
   - Most tests should be unit, fewer integration, fewest evaluation.
3. **Section 3 — Isolation from the outside world.**
   - Automated tests shall never call the real embedding, re-ranking, LLM or object-storage services, and never touch the production collection.
   - External clients shall be replaced with fakes or mocks.
   - Anything needing a real vector store shall use a throwaway collection that the test creates and removes.
4. **Section 4 — What every feature must cover.**
   - The normal path.
   - Bad input: empty, oversized, wrong type, malformed.
   - Failure of each external service: timeout and error.
   - Boundaries: no documents, one document, many documents.
5. **Section 5 — Tests guarding the Constitution.** These shall always exist and always pass:
   - Session isolation: one session can never retrieve another's chunks.
   - Idempotency: identical re-upload is skipped; changed re-upload replaces only that file.
   - Grounding: a question the documents cannot answer yields the "don't know" reply.
   - Prompt injection: instructions hidden in a document are not obeyed.
   - Stable identity: the same chunk always produces the same identifier.
6. **Section 6 — Quality of tests.**
   - Deterministic: no randomness, no dependence on time, order or the network.
   - One behaviour per test, named after the behaviour it proves.
   - Tests check outcomes, not implementation details, so refactors do not break them.
   - A flaky test is a defect; fix or remove it, never ignore it.
7. **Section 7 — Gates.**
   - The full suite shall pass before any commit is proposed for merging.
   - Coverage is a signal, not a goal; the critical paths in Section 5 matter more than a percentage.
   - Test dependencies are added through `uv` as development dependencies only.
8. **Section 8 — Honest reporting.**
   - Report test results exactly as they are, including failures and skipped tests.
   - Never weaken or delete a test just to make it pass.

## Article IX — Environment and Dependencies

1. **Section 1 — Single tool.** Dependencies and environments shall be managed with `uv` only: `uv sync`, `uv add`, `uv run`. Bare `pip install` into the project environment is prohibited.
2. **Section 2 — Lock integrity.** The manifest and lockfile shall stay consistent; the lockfile shall never be edited by hand.
3. **Section 3 — Deployment parity.** The deployment image installs from its own requirements file. Any dependency change shall regenerate that file so local and deployed environments match.
4. **Section 4 — Upgrades.** Library and model upgrades shall be checked against current official documentation first, and verified by running the application afterwards.

## Article X — Procedure and Amendment

1. **Section 1 — Branches.** Work shall occur on a named feature or fix branch, never directly on the main branch.
2. **Section 2 — Commits.** Commits shall be small, focused and clearly worded, and shall be made or pushed only when asked.
3. **Section 3 — Verification.** Work is not complete until its tests (Article VIII) pass and the application has been run with the changed behaviour exercised. Failures shall be reported as they are.
4. **Section 4 — Consultation.** Before any destructive or outward-facing act, and on any genuine ambiguity, the contributor shall ask.
5. **Section 5 — Amendment.** This Constitution shall be amended only by an explicit instruction from the project owner. Amendments shall stay concise: a rule that can be verified, stated once, in principle rather than in values.

## Article XI — Declared Shortfalls

The following fall short of Articles V and VIII today. They shall be corrected one at a time, on instruction, and not bundled into unrelated work.

1. Endpoints are unauthenticated and the session identifier is client-chosen.
2. No rate limiting; upload size is not uniformly bounded.
3. No relevance cutoff and no source citations in answers.
4. No automated test suite exists yet, and no automated evaluation of retrieval or generation.
5. The self-healing wrapper retries on any exception and can mask real faults.
6. The shared upload registry is not safe under concurrent instances.
7. Retrieval is semantic only, and the client libraries and models may be behind current releases.
8. Client-supplied chat history is trusted.
9. Locally written upload and registry files are never cleaned up.
