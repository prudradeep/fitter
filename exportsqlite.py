import sqlite3
import csv

db_path = "data/dta_c - Copy.db"
table_name = "knowledge_chunks"
output_file = "knowledge_chunks.csv"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute(f"SELECT * FROM {table_name}")

with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow([col[0] for col in cursor.description])
    writer.writerows(cursor.fetchall())

conn.close()

print(f"Exported {table_name} to {output_file}")