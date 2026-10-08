# Project Manager UI mockups

All prototypes use the BMS Midnight theme and illustrative data.

## Approved direction

### Map, Blocks, and Runs

Christian approved `07-map-blocks-runs` as the implementation direction. It keeps Research Map as the base composition, then adds the selected elements from the other variants:

- Project tree, relationship map, and selected-node inspector from Research Map;
- boxed Global Experiment and Domain Experiment regions from Experiment Canvas;
- run and replica presentation from Lab Notebook.

`06-tree-blocks-runs` was rejected because it retained only the tree from Research Map and replaced the map with a dashboard-like workspace.

The variants below remain reference material. They are not implementation authority.

| Variant | Spatial model | Primary strength |
|---|---|---|
| Experiment Canvas | Global Experiment tracks | Cross-domain work by objective |
| Research Map | Tree, relationship graph, inspector | Lineage and relationship clarity |
| Lab Notebook | ELN-style chapters and margin notes | Scientific context and decisions |

## Previous round

The Split Workbench and Compact Registry directions were rejected by Christian on 2026-08-08. Their files remain only as comparison references.

## Open

```bash
xdg-open docs/mockups/project-manager/03-experiment-canvas/index.html
xdg-open docs/mockups/project-manager/04-research-map/index.html
xdg-open docs/mockups/project-manager/05-lab-notebook/index.html
```

## Interaction checklist

### Experiment Canvas
- Expand any Global Experiment track.
- Open `Add existing` and attach the example record.

### Research Map
- Select map nodes and inspect the changing right pane.
- Open `Add to Project`.

### Lab Notebook
- Jump between notebook chapters.
- Open `Add existing` or an inline `Attach record` command.
