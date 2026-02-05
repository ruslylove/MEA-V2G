---
description: Deploy the MEA-V2G project to the BeagleBone Black
---

This workflow synchronizes the local project files to the BeagleBone at `beaglebone.local` using `rsync`.

// turbo
1. Run the deployment script:
```bash
./deploy_to_beaglebone.sh
```

2. After syncing, you can run the application on the BeagleBone:
```bash
ssh debian@beaglebone.local
cd ~/MEA-V2G
sudo .venv/bin/python3 Application.py <arguments>
```
