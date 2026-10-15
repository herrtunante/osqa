import markdown

class LimitedSyntaxExtension(markdown.Extension):
    def extendMarkdown(self, md):
        md.inlinePatterns.deregister("image_reference")

def makeExtension(**kwargs):
    return LimitedSyntaxExtension(**kwargs)

