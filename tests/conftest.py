from moneypit.auth import COOKIE_NAME


def create_test_user(db_module):
    """Create a test user in the DB and return the session cookie dict."""
    from moneypit.auth import hash_password, create_session
    with db_module.connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("test@test.com", hash_password("testtest")),
        )
        user_id = cur.lastrowid
        conn.execute(
            "UPDATE profiles SET user_id = ? WHERE user_id IS NULL",
            (user_id,),
        )
        token = create_session(conn, user_id)
    return {COOKIE_NAME: token}
