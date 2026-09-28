# Learning OS Core agent contract

This repository is the reusable **product Core** for Learning OS. It owns reusable product semantics, protocols, validation logic, synthetic tests, and reusable Domain assets. It is not live deployment authority, learner/Instance state, or a project/session state database.

## Authority and plane boundaries

- Current explicit user instructions and applicable safety constraints remain highest priority.
- Runtime-Control is authoritative for whether an exact Core commit is deployed. The current `main` branch is development state and does not self-deploy.
- Real learner state, Evidence, Instance state, credentials, private Control data, project/session writer state, and deployment pins do not belong in Core.
- Learning-specific Branch continuity and generation fencing remain product/runtime semantics where they protect learner-state continuity and writes.
- Agent/session/project-design collaboration state stays outside product Core unless a concrete product/runtime requirement independently justifies it. Do not introduce handoff/session machinery merely to preserve a particular Agent work surface.

## Mutation and verification
- Make implementation changes on a feature branch and publish them through a pull request; do not mutate protected `main` directly.
- Preserve the split Core / Instance / Runtime-Control trust model and target CAS / deployment fencing semantics.
- Use only synthetic fixtures in Core tests. Do not import real learner/private data for development or CI.
- Before proposing integration, run:
  - `python scripts/validate_learning_os.py . --core`
  - `python -m unittest discover -s tests -v`
- A green test or PR is verification evidence, not deployment, user acceptance, or promotion of the new Core commit.
