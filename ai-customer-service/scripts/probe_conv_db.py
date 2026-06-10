import sqlite3

paths = [
    r"D:\AliWorkbenchData\IMServiceDir\MessageSDK\3\aa8a52ecd41754f051cdfbbb0db1969c\conversation-0.db",
    r"D:\AliWorkbenchData\IMServiceDir\MessageSDK\3\3d1528a63d47c42b29a2f8aae7ab1551\conversation-0.db",
    r"D:\AliWorkbenchData\NewQNSDKData\3#27219251\user.db",
]

for db in paths:
    print(f"\n=== {db} ===")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print("Tables:", tables)
        for t in tables[:6]:
            cur.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in cur.fetchall()]
            print(f"  {t}: {cols}")
            try:
                cur.execute(f"SELECT * FROM {t} LIMIT 1")
                row = cur.fetchone()
                if row:
                    print(f"    SAMPLE: {str(row)[:300]}")
            except Exception as ex:
                print(f"    READ_ERR: {ex}")
        con.close()
    except Exception as e:
        print("OPEN_ERR:", e)
