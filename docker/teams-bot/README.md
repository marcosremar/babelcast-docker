<a id="readme-top"></a>

<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/Meeting-Baas">
    <img src="https://avatars.githubusercontent.com/u/141436269?s=200&v=4" alt="MeetingBaaS Logo" width="80" height="80">
  </a>

  <h3 align="center" style="margin-bottom: 0;">Meet Team Bot</h3>
  <p align="center" style="color: #999999; font-size: 14px;">by Meeting BaaS</p>
   <p align="center" style="color: #999999; font-size: 12px;">Meeting Bot as a Service</p>

  <p align="center">
    The most powerful <strong>open source meeting bot</strong> to join video calls, and record them.
    <br />
    100% free, self-hosted, and privacy-first.
    <br /><br />
    <a href="https://www.meetingbaas.com">🌐 Landing Page</a>
    &nbsp;•&nbsp;
    <a href="https://www.meetingbaas.com/demo">⚡ Live Demo</a>
    &nbsp;•&nbsp;
    <a href="https://discord.com/invite/dsvFgDTr6c">💬 Join Our Discord</a>
    &nbsp;•&nbsp;
    <a href="https://github.com/Meeting-Baas/meet-teams-bot/issues/new?labels=bug&template=bug_report.md">🐛 Report a Bug</a>
  </p>

  [![Contributors][contributors-shield]][contributors-url]
  [![Forks][forks-shield]][forks-url]
  [![Stargazers][stars-shield]][stars-url]
  [![Issues][issues-shield]][issues-url]

</div>

<p align="center">
  <b>🔥 Be among the first to support and star 🔥</b><br>
  <a href="https://github.com/Meeting-Baas/meet-teams-bot/stargazers">
    <b>Current: <img src="https://img.shields.io/github/stars/Meeting-Baas/meet-teams-bot?style=social" /> → Goal: 100 ⭐️</b>
  </a>
</p>

## What is Meet Team Bot?

**Meet Team Bot** is the open-source engine behind **Meeting BaaS**.
It lets you **send bots into video meetings** (Google Meet, Teams, Zoom) to **record**, **transcribe**, and **summarize** conversations — all while keeping your data **private** and **under your control**.

Supports:

- ✅ Google Meet
- ✅ Microsoft Teams

Use it as a free and self-hostable alternative to services like `recall.ai`.


## Try It Live

* 🌐 [Online Demo](https://your-demo-url.com)
* ⚡ [Test a Live Meeting Bot Now](https://your-demo-url.com/live)


## Get Started in Seconds

* 🎥 [Watch a Video Walkthrough](https://your-video-url.com)

### Clone the repo and build the Docker image

```bash
# Clone the repo
git clone https://github.com/Meeting-Baas/meet-teams-bot.git
cd meet-teams-bot

./run_bot.sh build
```

### Send a bot to record your meeting

```bash
# Basic usage (uses bot.config.json by default)
./run_bot.sh run

# Override any config param from the CLI
./run_bot.sh run meeting_url=https://meet.google.com/abc-defg-hij bot_name="My Bot"

# Use a custom config file
./run_bot.sh run my_custom_config.json meeting_url=https://meet.google.com/abc-defg-hij
```

- The config file is now `bot.config.json` (see it for example format).
- All logs print to stderr, so only clean JSON is sent to the bot.
- At the end, you'll see a message with your bot UUID for easy lookup of recordings.

### Retrieve the Recording

```bash
cd ./recordings
```

* 📘 [Full Developer Guide →](https://docs.meetingbaas.com/)

## Known Limitations

### Video Participants on Teams (Docker)

⚠️ **Current Docker Limitation**: When running the bot in Docker, you may not see video streams of other participants in Microsoft Teams meetings. This is due to browser compatibility (chromium) issues in the containerized environment.

✅ **Production Workaround**: For full video support, deploy the bot on a **real Ubuntu server with Google Chrome** installed. This provides optimal compatibility and performance.

🙋‍♂️ **Community Contribution Welcome**: We'd love a community PR that properly integrates Google Chrome into our Docker image!

## Dive Deeper

### The Meeting BaaS Ecosystem

Meeting BaaS is a powerful and **easy-to-integrate ecosystem** that provides you with **everything you need** to **capture, transcribe, and summarize meetings** — simply, privately, and at scale.

#### 🧩 Core Components

* **[`meet-teams-bot`](https://github.com/Meeting-Baas/meet-teams-bot)** – Bot to join Microsoft Teams meetings and stream audio.
* **[`realtime-meeting-transcription`](https://github.com/Meeting-Baas/realtime-meeting-transcription)** – Real-time transcription engine built for performance and privacy.
* **[`meeting-mcp`](https://github.com/Meeting-Baas/meeting-mcp)** – Protocol server for managing bots, transcripts, and recordings.

#### 🛠 Developer Tools

* **[`sdk-generator`](https://github.com/Meeting-Baas/sdk-generator)** – TypeScript SDK and MPC client generator to interact with the API effortlessly.

#### 📂 Apps & Interfaces

* **[`transcript-seeker`](https://github.com/Meeting-Baas/transcript-seeker)** – Browser-based transcript manager with note-taking, bot integration, and AI-powered chat.
* **[`speaking-meeting-bot`](https://github.com/Meeting-Baas/speaking-meeting-bot)** – Autonomous bots that can listen, speak, and interact in meetings.

📘 [Full Developer Guide →](https://docs.meetingbaas.com/)


## Community

Get updates on Meeting BaaS development and chat with the project maintainers and community members.

- Follow [@MeetingBaas on X (Twitter)](https://twitter.com/MeetingBaas)
- Follow [Lazare Rossillon on X (Twitter)](https://twitter.com/LaRossillon)
- Join our [community Discord](https://discord.com/invite/dsvFgDTr6c)
- Explore our [GitHub Discussions](https://github.com/Meeting-Baas/meet-teams-bot/discussions)

For questions, feedback, or to connect with the team, use the links above!


## Creators

**Lazare Rossillon 🐰**

- <https://www.linkedin.com/in/lazare-rossillon/>
- <https://github.com/Lazare-42>

**Philippe Drion 🦙**

- <https://www.linkedin.com/in/philippe-drion-3905a3a5/>

**Mickael Billod 🐮**

- <https://www.linkedin.com/in/mickael-billod-121217199/>


## Thanks

Thanks to [Playwright](https://github.com/microsoft/playwright) for providing the open-source browser automation framework that helps us test and automate meeting bots across browsers!

## First Stargazers

Huge thanks to our **founding stargazers** — the first developers who believed in the project and showed their support early. You helped make this real ❤️

[![Stargazers repo roster for @Meeting-Baas/meet-teams-bot](https://reporoster.com/stars/Meeting-Baas/meet-teams-bot)](https://github.com/Meeting-Baas/meet-teams-bot/stargazers)

## License

This project is licensed under the **Elastic License 2.0 (ELv2)**. See the [LICENSE.md](LICENSE.md) file for the full terms and conditions.

**Ecosystem Projects:** All other MeetingBaaS projects (Transcript Seeker, Speaking Bots, etc.) are MIT licensed.

<!-- MARKDOWN LINKS & IMAGES -->
<!-- https://www.markdownguide.org/basic-syntax/#reference-style-links -->

[contributors-shield]: https://img.shields.io/github/contributors/Meeting-Baas/meet-teams-bot.svg?style=for-the-badge
[contributors-url]: https://github.com/Meeting-Baas/meet-teams-bot/graphs/contributors

[forks-shield]: https://img.shields.io/github/forks/Meeting-Baas/meet-teams-bot.svg?style=for-the-badge
[forks-url]: https://github.com/Meeting-Baas/meet-teams-bot/network/members

[stars-shield]: https://img.shields.io/github/stars/Meeting-Baas/meet-teams-bot.svg?style=for-the-badge
[stars-url]: https://github.com/Meeting-Baas/meet-teams-bot/stargazers

[issues-shield]: https://img.shields.io/github/issues/Meeting-Baas/meet-teams-bot.svg?style=for-the-badge
[issues-url]: https://github.com/Meeting-Baas/meet-teams-bot/issues

[license-shield]: https://img.shields.io/github/license/Meeting-Baas/meet-teams-bot.svg?style=for-the-badge
[license-url]: https://github.com/Meeting-Baas/meet-teams-bot/blob/master/LICENSE
