import pyodbc

from app.core.config import settings


def main() -> None:
    conn_str = (
        f"DRIVER={{{settings.sqlserver_driver}}};"
        f"SERVER={settings.sqlserver_host},{settings.sqlserver_port};"
        f"UID={settings.sqlserver_username};"
        f"PWD={settings.sqlserver_password};"
        f"TrustServerCertificate=yes;"
    )

    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("SELECT @@VERSION")
    row = cursor.fetchone()
    print("Connected successfully!")
    print(row[0])
    conn.close()


if __name__ == "__main__":
    main()