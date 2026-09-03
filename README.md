<div align="center">
  <img src="ui/assets/glycofy-logo-lockup.png" alt="Glycofy" width="360">
  <h1>Athlete-aware AI meal planning</h1>
  <p>Glycofy turns an athlete’s profile, nutrition targets, and training schedule into practical daily and weekly meal plans—then carries those plans through cooking and grocery preparation.</p>
  <p><a href="https://app.glycofy.ai">Live application</a> · <a href="SECURITY.md">Security policy</a> · <a href="docs/RENDER_DEPLOYMENT.md">Deployment guide</a></p>
</div>

> [!IMPORTANT]
> Glycofy is under active development. It provides planning assistance, not medical advice, diagnosis, or treatment.

## Why Glycofy exists

Most meal planners treat every day alike. Athletes do not train that way.

Glycofy is being built around a different premise: nutrition planning should understand the work an athlete has completed, the sessions coming next, their body and performance goals, and the foods they can safely and realistically eat.

The result is a connected workflow:

```text
Athlete profile + training context
                ↓
      Energy and macro targets
                ↓
        AI-generated meal plan
                ↓
     Cooking guidance and swaps
                ↓
 Normalized, reviewable grocery list
```

## Current capabilities

- **Complete athlete onboarding** — units, body measurements, sex, date of birth, goals, timezone, dietary pattern, allergies, and ingredient exclusions.
- **Training-aware nutrition** — completed Strava activities and upcoming workouts inform recovery and fueling decisions.
- **Flexible training input** — users can connect Strava, enter workouts manually, or import planned workouts from a TrainingPeaks CSV export.
- **Daily and weekly AI planning** — structured meal recommendations target calories and macronutrients while enforcing dietary safety and weekly variety.
- **Transparent planning context** — Glycofy explains when recent or upcoming training data is missing and falls back to standard athlete targets.
- **Practical recipes** — meal ingredients, estimated cooking time, coordinated directions, and protein doneness guidance.
- **Meal control** — individual AI swaps, full-day planning, explanations, locking, and grocery exports.
- **Smart grocery preparation** — ingredients are consolidated across the week, compatible measurements are converted, pantry items are separated, household servings can be scaled, and reviewed lists can be approved as stable shopping snapshots.
- **Secure authentication** — email/password and Google account creation, server-side HTTP-only sessions, OAuth state validation, encrypted provider credentials, rate limiting, and security audit events.

## Product direction

The next major product layers are:

1. Package-aware grocery quantities and lower-waste ingredient reuse.
2. Grocery-delivery cart integrations after a weekly list is approved.
3. Meal completion, substitution, preference, hunger, recovery, and performance feedback.
4. Direct TrainingPeaks integration if API access becomes available.
5. Shared job infrastructure for scalable, cancellable weekly AI generation.

The long-term goal is a closed loop in which training informs nutrition, the plan becomes easy to shop and cook, and real-world athlete feedback improves the next plan.

## Architecture

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Web application | HTML, CSS, vanilla JavaScript | Responsive planning, training, profile, and grocery experiences |
| API | Python 3.11, FastAPI, Pydantic | Authentication, planning, integrations, validation, and application workflows |
| Persistence | SQLAlchemy, Alembic, PostgreSQL | User, training, nutrition, meal-plan, and approval data |
| AI planning | OpenAI structured generation | Dietary-safe recipes, weekly variety, cooking guidance, and macro-aware recommendations |
| Integrations | Google OAuth, Strava OAuth/webhooks, TrainingPeaks CSV | Identity and training context |
| Production | Docker, Render, GitHub Actions | Deployment, migrations, health checks, and verification gates |

The production entry point is `app.main:app`.

## Local development

### Prerequisites

- Python 3.11
- PostgreSQL for production-parity testing, or SQLite for a quick local start
- Node.js 22 for the JavaScript dependency audit

### Setup

```bash
git clone https://github.com/marcnester/glycofy-api.git
cd glycofy-api

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8090
```

Open [http://127.0.0.1:8090](http://127.0.0.1:8090).

The default development configuration uses SQLite. Configure `DATABASE_URL` with a PostgreSQL connection string when testing production behavior.

## Configuration

Configuration is read from environment variables or a local `.env` file. Start with [.env.example](.env.example).

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database connection string |
| `JWT_SECRET` | Session-signing secret; use at least 32 random characters |
| `OAUTH_TOKEN_ENCRYPTION_KEY` | Fernet key used to encrypt retained provider credentials |
| `OPENAI_API_KEY` | Enables AI meal recommendations |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google account authentication |
| `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` | Strava activity integration |
| `PUBLIC_BASE_URL` | Canonical public application URL |
| `ALLOWED_ORIGINS` / `ALLOWED_HOSTS` | Browser and host allowlists |

Never commit `.env`, databases, OAuth credentials, API keys, exported user data, or production logs. Production secrets belong in the hosting provider’s secret store.

## Database migrations

Apply the complete schema chain with:

```bash
alembic upgrade head
```

Render runs the same command as a pre-deploy step. New migrations should be safe on PostgreSQL and tested from an empty database through the current head.

## Verification

```bash
pytest -q
ruff check app tests
mypy app
bandit -q -lll -r app -x tests
pip-audit -r requirements.txt
npm test
```

GitHub Actions repeats the test, dependency, static-analysis, and full-history Gitleaks checks for pushes and pull requests.

## Security and privacy

Glycofy handles health-adjacent, dietary, allergy, and connected-account information. The application therefore uses a fail-closed production configuration, HTTP-only secure cookies, CSRF/origin controls, bounded request bodies, authentication and OAuth rate limits, encrypted OAuth tokens, redacted structured logging, and privacy-safe audit events.

Please do not open public issues containing vulnerabilities, credentials, personal information, activity exports, screenshots of private health data, or production logs. Follow [SECURITY.md](SECURITY.md) for the current security baseline and reporting guidance.

## Repository status and license

This repository is publicly visible to document Glycofy’s development and architecture. No open-source license is currently granted. Unless a license is added later, all rights are reserved and public visibility does not imply permission to copy, modify, or redistribute the code.
