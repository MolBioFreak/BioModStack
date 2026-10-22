# Native BNS installation integration

These are the native integration scripts used on ONT-platform, kept with the edition source. They connect an existing managed BNS installation to its desktop launchers, isolated annotation environment and vendor MinKNOW installation. They do not install OS packages or replace the locked Python/frontend runtime setup.

`--installation-root` is the existing prefix containing `source/`, `environment.sh`, the selected Python/frontend runtimes, and its launcher wrappers. The source checkout may be a symlink to your dedicated BNS Git checkout. Preserve an existing installation's environment selections.

```bash
python3 config/bns-native-installation/install-launchers.py --installation-root /path/to/bns-installation --check
python3 config/bns-native-installation/install-launchers.py --installation-root /path/to/bns-installation
python3 config/bns-native-installation/install-minknow-bridge.py --installation-root /path/to/bns-installation --check
python3 config/bns-native-installation/install-minknow-bridge.py --installation-root /path/to/bns-installation
```

Place `control-panel.sh` and `open-workspace.sh` in that prefix. For a new installation, copy `environment.sh.example` to `environment.sh` and select the actual managed toolchain paths. Do not overwrite an existing environment blindly. `plannotate-native.sh` belongs at `annotation/bin/plannotate` after provisioning the repository-pinned environment at `annotation/micromamba/envs/plannotate`.

The MinKNOW bridge requires the vendor interpreter at `/opt/ont/minknow/ont-python/bin/python`, uses localhost only and creates reconnect credentials in a private user configuration file. It preserves existing credentials. Restart the managed API/frontend through `source/start_ui.sh` after applying bridge configuration. Install the separately privileged reconnect socket using `config/mk1d-reconnect/install-native-root-helper.py` as documented there.

Machine environments, reconnect secrets, databases, annotations, instrument data, models, runtime images and generated test results stay outside Git. The bridge installation does not start a sequencing protocol or qualify NGS execution.
