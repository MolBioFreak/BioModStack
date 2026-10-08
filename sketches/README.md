# RFD3 Results Viewer mockups

These are disposable, interactive HTML sketches. They use representative fixture values for layout review. They do not change the BioModStack application.

## Open

From this directory:

```bash
xdg-open sketches/001-decision-first/index.html
xdg-open sketches/002-operator-workbench/index.html
xdg-open sketches/003-compare-workspace/index.html
```

## Comparison

| Variant | Main question | Density | Strongest use |
|---|---|---:|---|
| Decision first | Which candidate should I inspect next? | Medium | Fast batch triage |
| Operator workbench | Where is every technical control? | High | Repeated operator review |
| Compare workspace | What changed from source to candidate? | Medium | Structural interpretation |

## Recommendation

Use **Decision first** as the base. Add the **Compare workspace** source/candidate split as the primary structure mode. Take the **Operator workbench** tab and inspector treatment for Validation, Artifacts, and Provenance.
