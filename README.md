# IPO Pulse — Mainboard GMP Tracker

Automated Mainboard IPO GMP tracker powered by InvestorGain, Python, Playwright, GitHub Actions and GitHub Pages.

## What it does

- Fetches InvestorGain IPO GMP data.
- Keeps Mainboard IPOs only.
- Includes statuses `C`, `O`, `CT`, and `U`.
- Calculates estimated profit as `GMP × Lot`.
- Sorts by estimated profit, highest first.
- Color-codes rows:
  - Green = Open
  - Yellow = Upcoming
  - Red = Closed/other
- Generates a responsive HTML report.
- Uses the same HTML design to generate a full-page PNG with Playwright.
- Runs automatically at 7:00 AM, 1:00 PM and 11:30 PM IST.
- Publishes the report through GitHub Pages.

## GitHub Pages setup

In the repository:

**Settings → Pages → Build and deployment → Source → GitHub Actions**

The workflow already contains the Pages deployment configuration.

## Manual update

Open:

**Actions → Update IPO Pulse → Run workflow**

The entire process is automated; no local machine is required after the repository is configured.

## Data

Source: InvestorGain IPO GMP Live.

GMP is unofficial and can change at any time.
