# Discord Bot - Regear & Balance System

## Overview
This is a Discord bot designed for managing a guild's regear requests and balance system. It provides automated approval workflows, balance tracking, and loot splitting calculations.

## Project Architecture
- **Language**: Python 3.12
- **Framework**: discord.py 2.6.3
- **Type**: Discord Bot (Console Application)
- **Entry Point**: Bot.py

## Recent Changes (September 30, 2025)
- Imported from GitHub repository
- Configured for Replit environment
- Moved Discord token and configuration IDs to environment variables for security
- Set up workflow to run the bot
- Added proper error handling for missing environment variables

## Setup Instructions

### 1. Configure Discord Token (Required)
The bot requires a Discord bot token to run. You need to add it as a secret:

1. Open the **Secrets** tool in Replit (from the Tools menu or search bar)
2. Click "New Secret"
3. Add the following secret:
   - **Key**: `DISCORD_TOKEN`
   - **Value**: Your Discord bot token (from Discord Developer Portal)

### 2. Optional Configuration (Uses defaults if not set)
You can optionally override these settings by adding more secrets:

- **APPROVAL_CHANNEL_ID**: The Discord channel ID where approval requests are sent (default: 1422392394355052717)
- **ADMIN_ROLE_ID**: The Discord role ID that has admin permissions (default: 1422411404274565130)

### 3. Run the Bot
Once the DISCORD_TOKEN is configured, the bot will start automatically via the "Discord Bot" workflow.

## Bot Features

### Regear Request System
- Automatically detects messages with role keywords (DPS, TANK, HEALER, SUPPORT)
- Creates approval queue with buttons for admins to approve/reject/mark as pending
- Adds balance to approved requests
- Notifies users of approval status

### Balance Management Commands
**Admin Only:**
- `!addbal @user amount` - Add balance to a user
- `!balremove @user amount` - Remove balance from a user
- `!pagar @user` - Pay out user's balance and reset to 0

**All Users:**
- `!balance` or `!bal` - Check your balance
- `!top` - View top 10 players by balance

### Loot Split System
- `!split total players [silver_liquido]` - Calculate loot distribution
  - Automatically deducts 19% guild tax
  - Adds liquid silver if provided
  - Calculates per-player share

### Help Command
- `!helpbot` - Displays complete bot manual (Admin only)

## File Structure
- `Bot.py` - Main bot code
- `balances.json` - Balance data storage (auto-generated)
- `regear_data.json` - Regear request data (auto-generated)

## Role Values (Silver)
- DPS: 700,000
- TANK: 1,000,000
- HEALER: 600,000
- SUPPORT: 1,800,000

## User Preferences
- Original Discord IDs and configuration maintained from GitHub import
- Spanish language interface (bot messages in Spanish)
- Command prefix: `!`

## Security Notes
- Discord token is now stored securely in Replit Secrets
- Never commit the token to version control
- The original hardcoded token has been removed for security
