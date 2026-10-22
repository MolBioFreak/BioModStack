#!/usr/bin/env python3
"""Install the edition's user-owned desktop entries without changing system packages."""
import argparse
from pathlib import Path
import subprocess
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--installation-root', type=Path, required=True, help='Existing BNS installation prefix containing source and environment.sh')
parser.add_argument('--check', action='store_true', help='Validate installation paths without changing launchers, units or credentials')
args=parser.parse_args()
root=args.installation_root.expanduser().resolve()
assert (root/'source/platform/api/main.py').is_file(), 'The selected installation must contain its BNS source checkout'
assert (root/'environment.sh').is_file(), 'The selected installation must contain environment.sh'
if args.check:
    print('BNS installation paths are valid')
    raise SystemExit(0)
applications=Path.home()/'.local/share/applications'
applications.mkdir(parents=True,exist_ok=True)
desktop_result=subprocess.run(['xdg-user-dir','DESKTOP'],capture_output=True,text=True,check=True)
desktop=Path(desktop_result.stdout.strip())
desktop.mkdir(parents=True,exist_ok=True)
icon=root/'source/platform/assets/icons/biomodstack_tray.png'
entries=[
 ('org.bns.plasmid-toolkit.desktop','BNS Plasmid Development Toolkit',f'"{root}/control-panel.sh"','Start, stop and manage the local plasmid development platform'),
 ('bns-molbio.desktop','BNS MolBio Viewer',f'"{root}/open-workspace.sh" /designer','Open molecular biology and plasmid development tools'),
 ('bns-ngs.desktop','BNS NGS Workbench',f'"{root}/open-workspace.sh" /ngs','Open the local ONT and NGS workspace'),
]
for directory,names in [(applications,['org.biomodstack.panel.desktop','biomodstack-molbio.desktop','biomodstack-ngs.desktop']), (desktop,['BioModStack Control Panel.desktop','BioModStack MolBio Viewer.desktop','BioModStack NGS.desktop'])]:
 for name in names:
  path=directory/name
  if path.is_file() and str(root) in path.read_text():path.unlink()
for filename,name,command,comment in entries:
 text=f'''[Desktop Entry]
Version=1.0
Type=Application
Name={name}
Comment={comment}
Exec={command}
Path={root}/source
Icon={icon}
Terminal=false
Categories=Science;
StartupNotify=true
Keywords=BNS;Plasmid;MolBio;NGS;ONT;
'''
 if filename.startswith('org.'):text+='StartupWMClass=org.biomodstack.panel\n'
 for path in [applications/filename,desktop/(name+'.desktop')]:
  if path.exists() and str(root) not in path.read_text():raise RuntimeError(f'Refusing to replace another installation: {path}')
  path.write_text(text);path.chmod(0o755)
  subprocess.run(['desktop-file-validate',str(path)],check=True)
  if path.parent==desktop:subprocess.run(['gio','set',str(path),'metadata::trusted','true'],check=False,capture_output=True)
subprocess.run(['update-desktop-database',str(applications)],check=True)
print('Installed BNS Control Panel, MolBio Viewer and NGS Workbench launchers')
