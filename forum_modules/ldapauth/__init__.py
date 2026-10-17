try:
    import ldap
    import ldap.filter
    CAN_USE = True
except ImportError:
    # python-ldap is not installed: the module stays disabled.
    CAN_USE = False
