"""
Test script to verify database connection
Run this with: python test_db_connection.py
"""
from sqlalchemy import text
from app.core.database import engine, SessionLocal
from app.core.config import settings
from app.models.user import User

def test_connection():
    """Test basic database connection"""
    print("=" * 60)
    print("Testing PostgreSQL Database Connection")
    print("=" * 60)

    # Test 1: Check if we can connect
    print("\n1. Testing database connection...")
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
            print(f"✓ Connected successfully!")
            print(f"  PostgreSQL version: {version[:50]}...")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False

    # Test 2: Check if tables exist
    print("\n2. Checking if tables exist...")
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
            """))
            tables = [row[0] for row in result]
            print(f"✓ Found {len(tables)} table(s):")
            for table in tables:
                print(f"  - {table}")
    except Exception as e:
        print(f"✗ Failed to check tables: {e}")
        return False

    # Test 3: Check users table structure
    print("\n3. Checking users table structure...")
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'users'
                ORDER BY ordinal_position
            """))
            columns = result.fetchall()
            print(f"✓ Users table has {len(columns)} columns:")
            for col_name, data_type, nullable in columns:
                print(f"  - {col_name}: {data_type} ({'NULL' if nullable == 'YES' else 'NOT NULL'})")
    except Exception as e:
        print(f"✗ Failed to check table structure: {e}")
        return False

    # Test 4: Test CRUD operations
    print("\n4. Testing CRUD operations...")
    db = SessionLocal()
    try:
        # Count existing users
        user_count = db.query(User).count()
        print(f"✓ Current user count: {user_count}")

        # Try to create a test user
        test_user = User(
            email="test@example.com",
            username="testuser",
            hashed_password="fakehash123",
            is_active=True,
            is_superuser=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)
        print(f"✓ Created test user with ID: {test_user.id}")

        # Read it back
        retrieved_user = db.query(User).filter(User.id == test_user.id).first()
        print(f"✓ Retrieved user: {retrieved_user.username} ({retrieved_user.email})")

        # Update it
        retrieved_user.username = "updated_testuser"
        db.commit()
        print(f"✓ Updated username to: {retrieved_user.username}")

        # Delete it
        db.delete(retrieved_user)
        db.commit()
        print(f"✓ Deleted test user")

        # Verify deletion
        final_count = db.query(User).count()
        print(f"✓ Final user count: {final_count}")

    except Exception as e:
        print(f"✗ CRUD operations failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

    # Test 5: Verify database URL configuration
    print("\n5. Database configuration:")
    db_url = settings.DATABASE_URL
    # Hide password for security
    safe_url = db_url.split('@')[1] if '@' in db_url else db_url
    print(f"✓ Connected to: {safe_url}")

    print("\n" + "=" * 60)
    print("All tests passed! Database is properly connected.")
    print("=" * 60)
    return True

if __name__ == "__main__":
    test_connection()
