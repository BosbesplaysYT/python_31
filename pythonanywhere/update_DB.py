# update_DB.py

from sqlalchemy import inspect, text
from flask_sqlalchemy import SQLAlchemy

def update_tables():
    # Delay the import until the function is called
    from app import app

    from app import db  # Import db from your app module

    with app.app_context():
        inspector = inspect(db.engine)

        # Step 1: Ensure all tables exist first
        existing_tables = set(inspector.get_table_names())
        for table in db.metadata.sorted_tables:
            if table.name not in existing_tables:
                print(f"Creating missing table: {table.name}")
                table.create(db.engine)
        
        # Step 2: Add missing columns after ensuring all tables exist
        for table in db.metadata.sorted_tables:
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing_columns:
                    print(f"Adding missing column '{column.name}' to table '{table.name}'")
                    alter_stmt = text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type}')
                    with db.engine.connect() as conn:
                        conn.execute(alter_stmt)
                        conn.commit()

        print("Database schema update complete.")

if __name__ == "__main__":
    update_tables()