# Build the iPhone app from Ubuntu (no Mac anywhere)

Building an iOS app requires Apple's compiler, which only runs on macOS — but you
never need a Mac *computer*: this guide builds the app on a **GitHub Actions
macOS runner** (a free rented Mac, ~1 minute) and installs the result on your
iPhone **from Ubuntu**.

```
your Ubuntu PC ──git push──▶ GitHub macOS runner ──xcodebuild──▶ unsigned .ipa
                                     │                                 │
                                     │                      scripts/altserver-install.sh
                                     ▼                                 ▼
                            (no Apple account               signed + installed on
                             needed in CI)                  your iPhone
```

**Two ways to sign — pick one:**

| | [Path 1: free Apple ID](#path-1-free-apple-id-recommended) | [Path 2: paid account](#path-2-paid-apple-developer-program) |
|---|---|---|
| Cost | **Free** | €99/year |
| Apple paperwork | none | browser: certificate, App ID, device, profile |
| Signing validity | **7 days**, re-sign in ~1 min | 1 year |
| Extra tools | Docker (anisette server) + AltServer-Linux | none |
| Needs a Mac | no | no |

**What you need either way**

| | |
|---|---|
| iPhone | iOS **17.0+**, a Lightning/USB-C data cable (charge-only cables won't work) |
| Ubuntu tools | `libimobiledevice` + `ideviceinstaller` — otherwise `sudo apt install libimobiledevice-utils ideviceinstaller` |
| GitHub account | Public repos build macOS runners for free; private repos get 2,000 min/month (macOS counts 10×, so ~200 real minutes — one build is ~1 min) |

---

# Path 1 (free Apple ID) — recommended

No Apple account, no Mac, no browser forms. CI hands you an **unsigned** `.ipa`,
and [AltServer-Linux](https://github.com/NyaMisty/AltServer-Linux) signs it with
your free Apple ID and installs it over the USB pairing.

## 1. One-time setup

```bash
# Anisette data for Apple's login handshake (the public servers are unreliable)
docker run -d --restart unless-stopped --name anisette -p 6969:6969 dadoum/anisette-v3-server

# The AltServer binary
mkdir -p ~/altserver
curl -fsSL https://github.com/NyaMisty/AltServer-Linux/releases/download/v0.0.5/AltServer-x86_64 \
  -o ~/altserver/AltServer
chmod +x ~/altserver/AltServer
```

## 2. Install Googly Eyes

```bash
./scripts/altserver-install.sh          # downloads the newest .ipa and installs it
```

It finds the phone, downloads the latest build from Actions, then asks for your
Apple ID and password. Use an **app-specific password**
([appleid.apple.com → Sign-In and Security](https://appleid.apple.com)) rather than
your real one, and expect a prompt for your 2FA code.

## 3. On the phone (first time only)

1. **Settings → Privacy & Security → Developer Mode** → turn on, reboot. iOS requires
   this for development-signed apps.
2. **Settings → General → VPN & Device Management** → trust the *Apple Development* profile.
3. Open **Googly Eyes**.

## 4. Keep it alive

Free Apple IDs expire apps after **7 days** and allow only a few app installs per
week. Just run the same command again with a fresh build:

```bash
gh workflow run "iPhone app (.ipa)" && sleep 90 && ./scripts/altserver-install.sh
```

---

# Path 2 (paid Apple Developer Program)

Use this if you have the €99 membership: it signs for a full year with no Docker,
no AltServer and no weekly refresh. The CI job switches to it automatically as soon
as the three secrets exist.

## 1. Get your iPhone's UDID

Plug the phone in, unlock it, tap **Trust** if asked:

```bash
idevicepair pair
ideviceinfo | grep UniqueDeviceID
```

Copy the value (a 40-character hex string like `00008101-001A2B3C4D5E6F70`).
No cable handy? On the phone: **Settings → General → About → UDID** (tap to copy).

## 2. Create a signing request

Do this in a folder **outside** the repository, so the private key can never be
committed:

```bash
mkdir -p ~/bluey-signing && cd ~/bluey-signing
~/Documents/ChatGPT/bluey/scripts/ios-signing.sh csr "you@example.com"
```

(adjust the script path to wherever your repo lives). This writes `ios.key` (your
secret key — never share it, never commit it) and `ios.csr` (the request you upload).

## 3. The Apple paperwork (browser, ~10 minutes — **paid membership required**)

> Free Apple IDs cannot create certificates, App IDs, devices or profiles in this
> portal. Everything below needs the €99 Apple Developer Program; use Path 1 above
> if you don't have it.

Go to [developer.apple.com/account](https://developer.apple.com/account) and sign
in with your Apple ID. If it asks you to accept a license agreement, do that first.

1. **Certificates** → `+` → **Apple Development** → continue → *Upload CSR file* →
   pick `ios.csr` → continue → **Download** `development.cer`.
2. **Identifiers** → `+` → **App IDs** → **App** → continue →
   Description: `Googly Eyes`, Bundle ID: **Explicit** →
   **exactly** `co.visionairy.googly.phone` → continue → **Register**.
   (Leave capabilities alone; the app needs none.)
3. **Devices** → `+` → Name: `my iPhone`, Device ID: the UDID from step 1 →
   continue → **Register**.
4. **Profiles** → `+` → **iOS App Development** → continue →
   App ID `Googly Eyes` → your **Apple Development** certificate → your device →
   give it the name `Bluey iPhone Dev` → continue → **Generate** → **Download**
   `Bluey iPhone Dev.mobileprovision`.

> ⚠️ **Order matters.** Register the device *before* creating the profile, or the
> profile won't contain the phone. If you add a device later, re-do step 4 and
> update the `IOS_PROFILE_BASE64` secret (step 5).

## 4. Turn the certificate into a `.p12`

Keep the two downloaded files together with `ios.key` (browser downloads usually
land in `~/Downloads`):

```bash
cd ~/bluey-signing
~/Documents/ChatGPT/bluey/scripts/ios-signing.sh p12 ~/Downloads/development.cer ios.key ios.p12 "choose-a-password"
```

The script checks that the certificate really belongs to `ios.key`, and prints
the base64 line for the next step. Pick any password and remember it.

## 5. Put the repo on GitHub and add the secrets

Your repo has no remote yet, so create one (skip the middle lines if it already exists):

```bash
cd ~/Documents/ChatGPT/bluey
git config --global user.name  "Your Name"          # only if you never set it
git config --global user.email "you@example.com"
git add -A && git commit -m "Ubuntu port + iPhone CI build"
git branch -M main
git remote add origin https://github.com/YOUR_NAME/bluey.git
git push -u origin main
```

Then create the three secrets — with the [`gh` CLI](https://cli.github.com/) or in
the browser under **Settings → Secrets and variables → Actions → New repository secret**:

```bash
base64 -w0 ~/bluey-signing/ios.p12 > /tmp/p12.b64
base64 -w0 ~/Downloads/"Bluey iPhone Dev.mobileprovision" > /tmp/profile.b64

gh secret set IOS_P12_BASE64    < /tmp/p12.b64
gh secret set IOS_P12_PASSWORD   < <(printf '%s' 'choose-a-password')
gh secret set IOS_PROFILE_BASE64 < /tmp/profile.b64
```

In the browser, the secret *value* is simply the contents of the `.b64` file (one long line).

## 6. Build it

**Actions** tab → **iPhone app (.ipa)** → **Run workflow** → run. On a green run,
open it → *Artifacts* → download `GooglyEyes-ipa` → unzip → `GooglyEyes.ipa`.

## 7. Install it on the phone

```bash
./scripts/install-iphone.sh ~/Downloads/GooglyEyes.ipa
```

First launch only: on the phone go to **Settings → General → VPN & Device
Management → Apple Development: … → Trust**.

## 8. Pair with your Ubuntu desktop

1. Phone and PC on the **same Wi-Fi**; start `./scripts/run-desktop.sh`
   (log in on **Ubuntu on Xorg** if you want screenshots/pointing/computer control).
2. Open **Googly Eyes** on the phone — it finds the desktop by itself.
3. Approve the **"Connect an iPhone?"** dialog on the PC.
4. Double-tap Bluey to wake him, press and hold to ask him anything.

---

## Renewing every 7 days

Free Apple IDs expire development apps after **7 days** (a paid $99/yr account
lasts a year; the certificate itself is valid for 1 year). When the app stops opening:

1. **Actions** → *iPhone app (.ipa)* → **Run workflow** (~5 min)
2. Download the artifact, then `./scripts/install-iphone.sh GooglyEyes.ipa`

You only redo the browser steps (3–5) when you add a device, or yearly when the
certificate expires.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ideviceinfo`: *No device found* | Data cable, unlock the phone, tap **Trust**. Try another port/cable. |
| Workflow: *Secret IOS_… is missing* | Step 5 — the secret name must match exactly. |
| Workflow: *No signing certificate* | Wrong/expired `.p12` or wrong `IOS_P12_PASSWORD`. Redo step 4. |
| Workflow: *profile doesn't include the device* | Register the device (3.3), rebuild the profile (3.4), refresh `IOS_PROFILE_BASE64`. |
| Workflow: *No profiles for team …* / export fails | The profile changed — just update `IOS_PROFILE_BASE64`; the workflow reads its name automatically. |
| `ideviceinstaller`: *invalid / Could not install* | `ideviceinstaller uninstall co.visionairy.googly.phone` (older builds: `-U`), then install again — the script retries this for you. |
| Phone: *Untrusted Developer* | Settings → General → VPN & Device Management → Trust. |
| Phone: app opens then quits | Usually an expired build — renew (above). |
| App can't find the desktop | Same Wi-Fi, desktop running, no guest Wi-Fi; allow the **Local Network** permission (Settings → Privacy → Local Network). |

## Why this is safe

- Your OpenAI key never leaves your PC — the desktop mints short-lived tokens.
- `ios.key` never leaves your machine; the `.p12` and profile live in GitHub
  **secrets** (write-only, masked in logs).
- Keep the repo **private** if you'd rather not advertise it.

