/*
 * Syntax highlighting for code blocks in posts and in the editor preview.
 * Fenced blocks (```python) name their language; indented blocks are
 * auto-detected among the languages below.
 *
 * highlight.js itself (data-highlight-src of this script's tag) is loaded
 * only when a page has a code block to colour, after the page has loaded
 * (colours only, no layout change): pages without code never download it.
 * window.osqaHighlight(root) colours the blocks inside root (the live
 * question page calls it for new posts), loading the library if needed.
 */
(function() {
    var script = document.currentScript;
    var src = script && script.getAttribute('data-highlight-src');
    var loading = null;

    var SELECTOR = '.question-body pre code, .answer-body pre code, .wmd-preview pre code, #previewer pre code, ' +
                   '.revision .body pre code, .page-content pre code';

    function configure() {
        hljs.configure({
            ignoreUnescapedHTML: true,
            languages: ['python', 'r', 'sql', 'javascript', 'typescript', 'bash', 'shell', 'json', 'xml', 'css',
                        'java', 'c', 'cpp', 'csharp', 'php', 'ruby', 'go', 'yaml', 'ini', 'markdown', 'diff']
        });
    }

    function library() {
        if (!loading) {
            loading = new Promise(function(resolve, reject) {
                if (window.hljs) {
                    resolve();
                    return;
                }
                if (!src) {
                    reject(new Error('no highlight.js'));
                    return;
                }
                var tag = document.createElement('script');
                tag.src = src;
                tag.async = true;
                tag.onload = function() { resolve(); };
                tag.onerror = function() { loading = null; reject(new Error('highlight.js not loaded')); };
                document.head.appendChild(tag);
            }).then(configure);
        }
        return loading;
    }

    function highlight(root) {
        var blocks = Array.prototype.filter.call((root || document).querySelectorAll(SELECTOR), function(block) {
            return !block.dataset.highlighted;
        });
        if (!blocks.length) {
            return;
        }
        library().then(function() {
            blocks.forEach(function(block) {
                if (!block.dataset.highlighted && block.isConnected) {
                    hljs.highlightElement(block);
                }
            });
        }, function() { /* plain code blocks */ });
    }

    function watchPreview(preview) {
        var timer = null;
        new MutationObserver(function() {
            clearTimeout(timer);
            timer = setTimeout(function() { highlight(preview); }, 250);
        }).observe(preview, {childList: true, subtree: true});
    }

    function start() {
        highlight(document);
    }

    document.addEventListener('DOMContentLoaded', function() {
        var previews = document.querySelectorAll('.wmd-preview, #previewer');
        for (var i = 0; i < previews.length; i++) {
            watchPreview(previews[i]);
        }
    });
    if (document.readyState === 'complete') {
        start();
    } else {
        window.addEventListener('load', start);
    }

    window.osqaHighlight = highlight;
})();
