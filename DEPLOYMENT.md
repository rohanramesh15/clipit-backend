# Deploying Deadbird Backend to Fly.io

## Prerequisites

1. Install the Fly CLI: https://fly.io/docs/hands-on/install-flyctl/
2. Sign up/login to Fly.io:
   ```bash
   flyctl auth signup
   # or
   flyctl auth login
   ```

## Initial Deployment

### 1. Navigate to the repository root
```bash
cd project-deadbird-backend
```

**Note:** The `fly.toml` and active FastAPI application are both at this backend root.

### 2. Launch the app (first time only)
```bash
flyctl launch --no-deploy
```

When prompted:
- Use the existing `fly.toml` configuration (say yes)
- Choose your region (default: Boston/bos)
- Do NOT deploy yet (we need to set secrets first)

### 3. Create a Supabase PostgreSQL project

Create a Supabase project in the [Supabase dashboard](https://supabase.com/dashboard/projects). For production, copy its **Session pooler** connection string from **Connect**. It must use:

- the pooler hostname (`*.pooler.supabase.com`), which is IPv4-compatible;
- port `5432`; and
- the `postgres.<project-ref>` username.

Do not use the Direct connection (IPv6-only on the free tier) or the Transaction pooler on port `6543`.

### 4. Set required secrets

```bash
# Generate a secure secret key
flyctl secrets set SECRET_KEY=$(openssl rand -hex 32)

# Use the Supabase Session pooler URL from the dashboard.
# Keep this value out of source control and shell history where possible.
flyctl secrets set DATABASE_URL="postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"

# Optional: DeepL API key if you use translation features
flyctl secrets set DEEPL_API_KEY="your-deepl-api-key"

# Set your production frontend URL for CORS
flyctl secrets set ALLOWED_ORIGINS='["https://your-frontend-url.com","http://localhost:3000"]'
```

### 5. Deploy the application

```bash
flyctl deploy
```

### 6. Verify deployment

```bash
# Check app status
flyctl status

# View logs
flyctl logs

# Open your app in browser
flyctl open
```

Your API will be available at: `https://deadbird-backend.fly.dev`

## Updating Your Deployment

After making changes to your code:

```bash
cd project-deadbird-backend
flyctl deploy
```

## Managing Secrets

```bash
# List all secrets (values are hidden)
flyctl secrets list

# Update a secret
flyctl secrets set SECRET_KEY=new-value

# Remove a secret
flyctl secrets unset SECRET_NAME
```

## Database Migrations

Migrations run automatically on deployment via `start.sh`. The script runs:
```bash
alembic upgrade head
```

If you need to run migrations manually:
```bash
flyctl ssh console
cd /app
alembic upgrade head
```

## Monitoring

```bash
# View real-time logs
flyctl logs

# SSH into the running container
flyctl ssh console

# Check resource usage
flyctl status
flyctl vm status
```

## Important Notes

1. **Database Choice:**
   - Production uses Supabase PostgreSQL through its Session pooler.
   - Local development uses the Docker PostgreSQL container; do not point it at the production database.

2. **CORS Configuration:**
   - Update `ALLOWED_ORIGINS` secret with your production frontend URL
   - The config in `app/core/config.py` has localhost URLs for development

3. **Health Checks:**
   - The app uses `/api/health` endpoint for health checks
   - Make sure this endpoint is working properly

4. **Scaling:**
   - Current config uses auto-stop/auto-start to minimize costs
   - Adjust `min_machines_running` in `fly.toml` if you need always-on service

## Troubleshooting

### Check logs
```bash
flyctl logs
```

### Access the container
```bash
flyctl ssh console
```

### Restart the app
```bash
flyctl apps restart deadbird-backend
```

### Check environment variables
```bash
flyctl ssh console
env | grep DATABASE_URL
```

## Cost Optimization

The current configuration uses:
- 1 shared CPU
- 1GB RAM
- Auto-stop when idle (0 minimum machines)

This should fit within Fly.io's free tier for hobby projects.
