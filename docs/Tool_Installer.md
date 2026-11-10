# Tool installer

BioModStack ships its own code: the interface, the API, the workflow framework,
the robot control code and the viewer. It does not ship or host the scientific
tools it runs. Each tool is built on your machine, from that tool's normal
upstream home, by a recipe in `tools/recipes/`. You can also point BioModStack
at an image you built yourself.

This guide is written for a person at a terminal and for an AI assistant or
script acting on that person's behalf. Every command behaves the same for both.

## Choosing what to install

```sh
./start_ui.sh tools modules              # the parts you can choose, and what each needs
./start_ui.sh tools select ont structure_prediction
./start_ui.sh tools list --selected      # only the tools for what you chose
```

The interface, the molecular biology tools, the viewer and the robot control
code are part of the repository and need no build. Every other part needs the
tools listed against it. A part with no recipe yet shows `no-recipe-yet`.

## Commands

```sh
./start_ui.sh tools list                 # every tool and its state here
./start_ui.sh tools plan dorado          # what will be fetched and built, sizes, licences
./start_ui.sh tools build dorado         # build, fetch selected weights, run the checks
./start_ui.sh tools build dorado --detach    # same, in the background
./start_ui.sh tools status dorado        # state, failing step, reason, log path
./start_ui.sh tools log dorado --tail 40
./start_ui.sh tools adopt dorado /path/to/my-dorado.sif   # use your own image
./start_ui.sh tools verify dorado        # re-run the checks on what is installed
./start_ui.sh tools remove dorado        # remove what the installer put in place
```

Add `--json` to any command for one JSON document (`schema: bms.tools.v1`).
Exit code 0 means the command did what it says. Exit code 3 means blocked or
failed, and the output carries `reason_code`, `message` and `log_path`.

Images go to the configured `container_dir` and weights to `weights_root`
(install profile, or `BMS_CONTAINER_DIR` / `BMS_WEIGHTS`). Installer state and
logs live under the BioModStack configuration directory in `tools-v1/<tool>/`.

## States

| State | Meaning |
| --- | --- |
| `not-installed` | no image for this tool is present |
| `building` | a build is running now |
| `failed` | the last build or check failed; see `reason_code` and the log |
| `incomplete` | some images or required weights are missing |
| `present-unverified` | files are there but the checks have not run here |
| `ready` | the checks passed on this machine with the current recipe |
| `ready-recipe-changed` | the checks passed, but the recipe has changed since |

Ready is a statement about this machine. It is not a claim that the image is
byte-identical to anyone else's, and it is not a scientific validation.

Checks that need an NVIDIA GPU are skipped, and reported as
`skipped-no-gpu`, on a machine without one.

## Recipe labels

- `builds-from-clean`: built and checked from nothing on a clean machine.
- `starting-point`: the best known steps, not yet proven from clean. Expect to
  fix things. This is where your own AI assistant is most useful.

## When a build fails

1. `./start_ui.sh tools status TOOL --json` gives the failing `step` and `reason_code`.
2. `./start_ui.sh tools log TOOL --tail 80` gives the build output.
3. Fix the cause (often the `.def` file named in `tools plan`), then run `build`
   again. A failed image never replaces a working one, and weight downloads resume.

Reason codes: `apptainer_missing`, `disk_space_low`, `recipe_definition_missing`,
`image_build_failed`, `smoke_test_failed`, `license_not_accepted`,
`weights_manual_step`, `weights_download_failed`, `weights_hash_mismatch`,
`weights_incomplete`, `weights_missing`, `image_missing`, `build_already_running`,
`build_interrupted`, `unknown_tool`, `unknown_module`, `recipe_invalid`.

Weights under a licence are fetched only after you pass
`--accept-license ID` for the licence shown by `tools plan`.

## Using your own image

If you already have a working image, `tools adopt TOOL /path/to/image.sif`
links it into place (or copies it with `--copy`) and runs the same checks.
`tools remove` never deletes an original you adopted, and never deletes an image
the installer did not put there.

## Adding or fixing a recipe

A recipe is one JSON file, `tools/recipes/<id>.json`, plus an Apptainer
definition file in the repository.

```json
{
  "schema": "bms.tool-recipe.v1",
  "id": "mytool",
  "name": "My Tool",
  "label": "starting-point",
  "modules": ["structure_prediction"],
  "upstream": {"homepage": "https://github.com/org/mytool"},
  "license": {"id": "MIT", "url": "https://github.com/org/mytool/blob/main/LICENSE"},
  "images": [{
    "filename": "mytool.sif",
    "def": "apptainer/mytool.def",
    "approx_size_gb": 8,
    "sources": ["docker base image", "git repository @ commit"],
    "smoke": [{"name": "help", "argv": ["mytool", "--help"]},
              {"name": "gpu", "gpu": true, "argv": ["python", "-c", "import torch; assert torch.cuda.is_available()"]}]
  }],
  "weights": [{
    "id": "main",
    "required": true,
    "license": {"id": "CC-BY-4.0", "url": "https://..."},
    "present_if": ["mytool/model.pt"],
    "fetch": {"type": "url", "files": [{"url": "https://upstream/model.pt", "dest": "mytool/model.pt", "sha256": "..."}]}
  }]
}
```

Rules:

- Point at the tool's own upstream home. Do not point at a private mirror.
- Pin what you can: base image digest, git commit, package versions, file hashes.
- `filename` must be the image name BioModStack already expects for that tool.
- `weights.fetch.type` is `url` (https file with optional hash), `container`
  (run the tool's own downloader inside the image; the weights root is mounted
  at `/weights`) or `manual` (give `instructions`).
- The definition's `%test` section runs as the first check. Add `smoke` commands
  for anything else that must work.
- Keep `starting-point` until the recipe has been built from nothing on a clean
  machine, then change it to `builds-from-clean`.

## How an image is produced

Each image in a recipe names exactly one way of getting it.

- `def`: an Apptainer definition in this repository, built with `apptainer build`. Optional `build_args` are passed as `--build-arg`; the text `{container_dir}` in a value becomes this install's container folder. Optional `build_dir` is the folder the build runs in, for definitions that copy files with relative paths.
- `pull`: an image the tool's own authors publish. `uri` is either `docker://…` (converted to a `.sif`) or an `https://` file with a `sha256`.
- `docker`: a Dockerfile in this repository (`file`, `tag`, optional `context`), built with Docker or Podman. With a `filename` ending in `.sif` the result is converted to a `.sif`; without one the image stays in Docker under its tag. Set `BMS_TOOLS_CONTAINER_RUNTIME=docker` or `podman` to choose.
- `script`: a provisioning script in this repository that owns its own layout (`argv` to run it, `check_argv` to tell whether its output is present).

Two more fields apply to any image. `needs` lists `.sif` files that must already be in the container folder, because the image is built on top of them; a missing one stops the build with `base_image_missing`. `requires_files` lists build inputs that are not shipped; a missing one blocks the plan with `build_input_missing` and prints `requires_note`.

New reason codes: `docker_missing`, `base_image_missing`, `build_input_missing`, `image_download_failed`, `image_hash_mismatch`, `adopt_not_applicable`.

## What the shipped recipes cover

Every image BMS uses has a recipe. Only Dorado, Boltz-2, Protenix and GROMACS are labelled `builds-from-clean`. The rest are `starting-point`: written from the definitions and upstream sources in this repository, never built from clean by us.

Known gaps, stated in the recipe notes as well:

- `ont-signal-tools` and `ont-clone-validation`: the signal workbench and the clone-validation validator compare an image id or file hash against a value recorded in this repository. An image you build or convert yourself gets its own id.
- `md-preparation`: the definition unpacks a conda-pack archive that is not shipped.
- `confornets`: this repository has no definition for the base image, only for the patch on top of it.
- Several definitions install a PyTorch nightly, which upstream withdraws after a while. Pin a release if that step fails.
- Weights marked as a manual step have no single public download address we could pin; the recipe says where to get them and where to put them.

## Locally built images are accepted as they are

BMS does not compare an image with an id or file hash recorded in this repository.
The ONT signal tools run whatever image exists under the tool's tag (the tag the
recipe builds), and the clone-validation runtime runs the image files that are in
its cache folder. The id or hash of the image that actually ran is written into the
job record, together with whether it equals the reference build.
