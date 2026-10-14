NAME = 'Mysql Full Text Search'
DESCRIPTION = "Enables Mysql full text search functionality."

# Not ported to Python 3 / Django 5 yet (it relies on APIs Django removed:
# the __search lookup, commit_unless_managed, fields without on_delete), so it
# stays off; search uses the built-in keyword matching (forum/utils/similar.py)
# and, with the ai module, semantic search.
CAN_USE = False
