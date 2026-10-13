/*
 * Syntax highlighting for code blocks in posts and in the editor preview.
 * Fenced blocks (```python) name their language; indented blocks are
 * auto-detected among the languages below.
 */
(function() {
    if (!window.hljs) {
        return;
    }

    hljs.configure({
        ignoreUnescapedHTML: true,
        languages: ['python', 'r', 'sql', 'javascript', 'typescript', 'bash', 'shell', 'json', 'xml', 'css',
                    'java', 'c', 'cpp', 'csharp', 'php', 'ruby', 'go', 'yaml', 'ini', 'markdown', 'diff']
    });

    var SELECTOR = '.question-body pre code, .answer-body pre code, .wmd-preview pre code, #previewer pre code, ' +
                   '.revision .body pre code, .page-content pre code';

    function highlight(root) {
        var blocks = (root || document).querySelectorAll(SELECTOR);
        for (var i = 0; i < blocks.length; i++) {
            if (!blocks[i].dataset.highlighted) {
                hljs.highlightElement(blocks[i]);
            }
        }
    }

    function watchPreview(preview) {
        var timer = null;
        new MutationObserver(function() {
            clearTimeout(timer);
            timer = setTimeout(function() { highlight(preview); }, 250);
        }).observe(preview, {childList: true, subtree: true});
    }

    document.addEventListener('DOMContentLoaded', function() {
        highlight(document);
        var previews = document.querySelectorAll('.wmd-preview, #previewer');
        for (var i = 0; i < previews.length; i++) {
            watchPreview(previews[i]);
        }
    });

    window.osqaHighlight = highlight;
})();
