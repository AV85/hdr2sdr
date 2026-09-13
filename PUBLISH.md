# Publishing to GitHub (one-time)

The repository https://github.com/AV85/hdr2sdr already exists. From the unpacked project folder:

```bash
cd hdr2sdr
git init
git add .
git commit -m "hdr2sdr 1.0.0"
git branch -M main
git remote add origin https://github.com/AV85/hdr2sdr.git
git push -u origin main
```

If the remote repository already contains a README or LICENSE created on GitHub,
pull it first: `git pull origin main --allow-unrelated-histories` (keep the local files on conflicts), then push.

Later updates:

```bash
git add .
git commit -m "describe the change"
git push
```
