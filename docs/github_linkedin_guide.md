# Beginner publication guide

This copy is local only. No remote repository or public post has been created.
The firmware GPL license is included; first review the remaining
component-license/provenance decisions in
[`license_and_credits.md`](license_and_credits.md).

## 1. Review the release folder

Open `github_release/README.md`, scan the folder contents, and confirm that no
credentials, local audio, or unwanted personal information are present. Check
that `firmware/LeoBuddy/LICENSE` and the firmware attribution header remain in
place. Preview all photos/video before choosing them for public use.

## 2. Create a GitHub repository

1. Sign in or create a GitHub account.
2. Choose **New repository** and name it `Leo-Buddy-AI-Robot` (or your choice).
3. Suggested description: “Raspberry Pi voice assistant with ESP32 animated
   OLED face, dual servos, and switch-triggered USB serial control.”
4. Suggested topics: `raspberry-pi`, `esp32`, `voice-assistant`, `embedded-systems`,
   `oled`, `robotics`, `python`, `arduino`.
5. Set visibility to Public only after license and file review. Do not ask
   GitHub to initialize a README/license if you plan to push this prepared
   repository; that can create unrelated initial history.
6. Do not configure a remote or push until you explicitly approve publication.

## 3. Prepare the first commit locally

The curated `github_release/` folder has its own new local Git repository and
no remote. After deciding the Raspberry Pi application's license and
provenance, inspect `git status` and `git diff --cached` before committing.
The firmware license is already in `firmware/LeoBuddy/LICENSE`. Suggested
initial commit message:

```text
feat: publish Leo Buddy Raspberry Pi and ESP32 integration
```

When ready, connect the remote you created and push the branch shown by
`git branch --show-current`. Never force-push. If Git is new to you, use GitHub
Desktop to add the local `github_release` folder, review the changed-file list,
enter the commit message, and push only after the final checklist.

## 4. Confirm publication

Open the repository in a private/incognito window and verify README rendering,
firmware path, docs links, and that the file list contains no `.env`, audio,
backups, virtualenv, binaries, or credentials. Copy the repository URL from the
browser. Add an external demo-video link only after the video is published.

## 5. LinkedIn

1. Review `linkedin_drafts.md` and replace `[GitHub repository URL]` after
   publication.
2. Post the project video first. Use real footage, clear subtitles, and no
   exposed API keys or private reminders.
3. Add the GitHub repository URL to the post and optionally to the **Projects**
   section of your profile.
4. Publish the technical post about a week later, optionally with a real
   wiring/hardware photo.
5. Check every image/video and screen recording for secret values before
   publishing. If a key was ever exposed, revoke it at the provider and create
   a replacement; deleting it from a file alone is not enough.

## Release checklist

- [ ] License selected and code provenance/permissions confirmed.
- [ ] No secrets, credentials, private recordings, or unsuitable logs.
- [ ] Python tests and firmware compile re-run from the release copy.
- [ ] Wiring table checked against the actual assembled hardware.
- [ ] Real photos/video captured and approved, or documented as forthcoming.
- [ ] README/docs/repository name and topics reviewed.
- [ ] GitHub remote and public visibility approved by the owner.
- [ ] Initial commit reviewed before push.
- [ ] LinkedIn drafts approved and repository link inserted.
