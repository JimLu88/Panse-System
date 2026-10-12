"""Scope inherited custom corrections to the current failed signup rows."""

def correction_row(pair, current_rows, protected_signup):
    row = current_rows.get(pair)
    if row is not None:
        return row
    # Successful items were deliberately excluded from this generation. Their
    # historical correction remains in the immutable run but must not re-enroll.
    if protected_signup.get(pair[0]) == 'success':
        return None
    # Unknown, missing and otherwise excluded rows are not proven successful.
    raise ValueError('custom_correction_outside_current_unsuccessful_scope')
