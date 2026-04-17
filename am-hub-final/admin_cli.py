#!/usr/bin/env python
"""
Admin CLI for AM Hub
Management commands for users, data, and system

Usage:
    python admin_cli.py user create --email user@example.com --password secret --name "John" --role manager
    python admin_cli.py user list
    python admin_cli.py sync all
    python admin_cli.py db seed
    python admin_cli.py health
"""

import os
import sys
import asyncio
import logging
from datetime import datetime
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

# Setup path
sys.path.insert(0, os.path.dirname(__file__))

from database import SessionLocal, engine, Base, init_db
from models import User, Client, Account
from auth import create_user, hash_password
from schemas import UserCreate
from config import settings, validate_config

logger = logging.getLogger(__name__)
console = Console()

app = typer.Typer(help="AM Hub Admin CLI")


@app.callback()
def setup():
    """Setup logging"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


# ============================================================================
# USER COMMANDS
# ============================================================================

@app.command()
def user_create(
    email: str = typer.Option(..., help="User email"),
    password: str = typer.Option(..., help="User password"),
    name: str = typer.Option(..., help="User full name"),
    role: str = typer.Option("manager", help="User role: admin, manager, viewer"),
    phone: Optional[str] = typer.Option(None, help="User phone"),
):
    """Create a new user"""
    try:
        console.print(f"🔓 Creating user: {email}", style="yellow")
        
        with SessionLocal() as db:
            # Check if exists
            existing = db.query(User).filter(User.email == email).first()
            if existing:
                console.print(f"❌ User already exists: {email}", style="red")
                return
            
            # Validate role
            valid_roles = ["admin", "manager", "viewer"]
            if role not in valid_roles:
                console.print(f"❌ Invalid role. Must be one of: {valid_roles}", style="red")
                return
            
            # Create user
            user_data = UserCreate(
                email=email,
                name=name,
                password=password,
                role=role,
                phone=phone,
            )
            
            new_user = User(
                email=email,
                name=name,
                phone=phone,
                role=role,
                password_hash=hash_password(password),
                created_at=datetime.now(),
            )
            
            db.add(new_user)
            db.commit()
            db.refresh(new_user)
            
            console.print(f"✅ User created successfully", style="green")
            console.print(f"   ID: {new_user.id}")
            console.print(f"   Email: {new_user.email}")
            console.print(f"   Role: {new_user.role}")
    
    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")


@app.command()
def user_list():
    """List all users"""
    try:
        console.print("🔍 Fetching users...", style="yellow")
        
        with SessionLocal() as db:
            users = db.query(User).all()
            
            if not users:
                console.print("No users found", style="dim")
                return
            
            table = Table(title=f"Users ({len(users)})")
            table.add_column("ID", style="cyan")
            table.add_column("Email", style="cyan")
            table.add_column("Name")
            table.add_column("Role", style="magenta")
            table.add_column("Created", style="dim")
            
            for user in users:
                created = user.created_at.strftime("%Y-%m-%d") if user.created_at else "-"
                table.add_row(
                    str(user.id),
                    user.email,
                    user.name or "-",
                    user.role,
                    created,
                )
            
            console.print(table)
    
    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")


@app.command()
def user_delete(email: str = typer.Option(..., help="User email to delete")):
    """Delete a user"""
    try:
        confirm = typer.confirm(f"⚠️  Are you sure you want to delete {email}?")
        if not confirm:
            console.print("Cancelled", style="dim")
            return
        
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == email).first()
            if not user:
                console.print(f"❌ User not found: {email}", style="red")
                return
            
            db.delete(user)
            db.commit()
            console.print(f"✅ User deleted: {email}", style="green")
    
    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")


# ============================================================================
# SYNC COMMANDS
# ============================================================================

@app.command()
def sync_all():
    """Run all integrations sync"""
    async def _sync():
        try:
            console.print("🔄 Starting full sync...", style="yellow")
            
            from integrations.airtable import get_clients as sync_airtable
            from integrations.merchrules_extended import (
                fetch_account_analytics,
                fetch_checkups,
                fetch_roadmap_tasks,
            )
            
            # Sync Airtable clients
            console.print("  📋 Syncing Airtable clients...", style="cyan")
            clients = await sync_airtable()
            console.print(f"    ✅ Fetched {len(clients)} clients")
            
            # Sync Merchrules
            console.print("  📊 Syncing Merchrules analytics...", style="cyan")
            analytics = await fetch_account_analytics()
            console.print(f"    ✅ Fetched {len(analytics) if analytics else 0} analytics")
            
            console.print("✅ Full sync completed", style="green")
        
        except Exception as e:
            console.print(f"❌ Sync error: {e}", style="red")
    
    asyncio.run(_sync())


@app.command()
def sync_clients():
    """Sync clients from Airtable"""
    async def _sync():
        try:
            console.print("🔄 Syncing Airtable clients...", style="yellow")
            
            from integrations.airtable import get_clients
            clients = await get_clients()
            
            with SessionLocal() as db:
                synced = 0
                for client_data in clients:
                    existing = db.query(Client).filter(
                        Client.email == client_data.get("email")
                    ).first()
                    
                    if not existing:
                        new_client = Client(
                            name=client_data.get("name"),
                            email=client_data.get("email"),
                            phone=client_data.get("phone"),
                            segment=client_data.get("segment", "smb"),
                            status="active",
                            health_score=75,
                            created_at=datetime.now(),
                        )
                        db.add(new_client)
                        synced += 1
                
                db.commit()
            
            console.print(f"✅ Synced {synced} new clients from Airtable", style="green")
        
        except Exception as e:
            console.print(f"❌ Sync error: {e}", style="red")
    
    asyncio.run(_sync())


# ============================================================================
# DATABASE COMMANDS
# ============================================================================

@app.command()
def db_init():
    """Initialize database schema"""
    try:
        console.print("🗄️  Initializing database...", style="yellow")
        init_db()
        console.print("✅ Database initialized", style="green")
    
    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")




@app.command()
def db_drop():
    """Drop all tables (DANGEROUS!!)"""
    try:
        confirm = typer.confirm("⚠️  ⚠️  ⚠️  This will delete ALL data! Are you absolutely sure?")
        if not confirm:
            console.print("Cancelled", style="dim")
            return
        
        confirm2 = typer.confirm("⚠️  This is your last chance. Confirm again:")
        if not confirm2:
            console.print("Cancelled", style="dim")
            return
        
        console.print("🗑️  Dropping all tables...", style="yellow")
        Base.metadata.drop_all(engine)
        console.print("✅ All tables dropped", style="green")
    
    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")


# ============================================================================
# SYSTEM COMMANDS
# ============================================================================

@app.command()
def health():
    """Check system health"""
    try:
        console.print("🏥 Checking system health...", style="yellow")
        
        # Check configuration
        valid, msg = validate_config()
        cfg = settings()
        
        console.print("\n📋 Configuration:")
        console.print(f"   Environment: {cfg.ENV}")
        console.print(f"   Debug: {cfg.DEBUG}")
        console.print(f"   Email Provider: {cfg.EMAIL_PROVIDER}")
        console.print(f"   Log Level: {cfg.LOG_LEVEL}")
        
        console.print("\n✅ System Health: OK" if valid else f"\n❌ Issues: {msg}", 
                     style="green" if valid else "red")
        
        # Show database stats
        with SessionLocal() as db:
            users = db.query(User).count()
            clients = db.query(Client).count()
            
            console.print("\n📊 Database Stats:")
            console.print(f"   Users: {users}")
            console.print(f"   Clients: {clients}")
    
    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")


@app.command()
def version():
    """Show version information"""
    cfg = settings()
    console.print(f"{cfg.APP_NAME} v{cfg.APP_VERSION}")
    console.print(f"Environment: {cfg.ENV}")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    app()
