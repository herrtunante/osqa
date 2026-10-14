NAME = 'Postgresql Full Text Search'
DESCRIPTION = "Enables PostgreSql full text search functionality."

# Not ported to Python 3 / Django 5 yet (it relies on APIs Django removed,
# such as commit_unless_managed), so it stays off; search uses the built-in
# keyword matching (forum/utils/similar.py) and, with the ai module, semantic search.
CAN_USE = False
