/*
 * Paste or drop images into the post editor: they are uploaded with the
 * same endpoint as the editor's image dialog and inserted as Markdown.
 */
(function() {
    var counter = 0;

    function text(key, fallback) {
        return (window.messages && messages[key]) || fallback;
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name=csrf-token]');
        return meta ? meta.content : '';
    }

    function refresh(editor) {
        editor.dispatchEvent(new Event('input', {bubbles: true}));
        editor.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
    }

    function insertAtCursor(editor, snippet) {
        var start = editor.selectionStart, end = editor.selectionEnd;
        var before = editor.value.slice(0, start), after = editor.value.slice(end);
        var pre = before && !/\n\n$/.test(before) ? (before.slice(-1) === '\n' ? '\n' : '\n\n') : '';
        var post = after && !/^\n/.test(after) ? '\n\n' : '\n';
        editor.value = before + pre + snippet + post + after;
        var caret = (before + pre + snippet + post).length;
        editor.setSelectionRange(caret, caret);
        refresh(editor);
    }

    function replacePlaceholder(editor, placeholder, replacement) {
        var pos = editor.value.indexOf(placeholder);
        if (pos < 0) {
            return;
        }
        editor.value = editor.value.slice(0, pos) + replacement + editor.value.slice(pos + placeholder.length);
        refresh(editor);
    }

    function showError(editor, message) {
        var box = editor.parentNode.querySelector('.upload-error');
        if (!box) {
            box = document.createElement('div');
            box.className = 'upload-error';
            box.setAttribute('role', 'alert');
            editor.parentNode.insertBefore(box, editor.nextSibling);
        }
        box.textContent = message;
        clearTimeout(box.timer);
        box.timer = setTimeout(function() { box.remove(); }, 8000);
    }

    function upload(editor, file) {
        var name = (file.name || 'image.png').replace(/[\[\]]/g, '');
        var alt = name.replace(/\.[^.]+$/, '') || 'image';
        var placeholder = '![' + text('uploading', 'Uploading') + ' ' + name + ' #' + (++counter) + '...]()';
        insertAtCursor(editor, placeholder);

        var data = new FormData();
        data.append('file-upload', file, name);
        fetch(messages.upload_url, {
            method: 'POST',
            body: data,
            credentials: 'same-origin',
            headers: {'X-CSRFToken': csrfToken()}
        }).then(function(response) {
            return response.text();
        }).then(function(body) {
            var xml = new DOMParser().parseFromString(body, 'application/xml');
            var url = (xml.querySelector('file_url') || {}).textContent;
            var error = (xml.querySelector('error') || {}).textContent;
            if (url && !error) {
                replacePlaceholder(editor, placeholder, '![' + alt + '](' + url + ')');
            } else {
                replacePlaceholder(editor, placeholder, '');
                showError(editor, error || text('upload_failed', 'The image could not be uploaded.'));
            }
        }).catch(function() {
            replacePlaceholder(editor, placeholder, '');
            showError(editor, text('upload_failed', 'The image could not be uploaded.'));
        });
    }

    function images(list) {
        return Array.prototype.filter.call(list || [], function(f) {
            return f && f.type && f.type.indexOf('image/') === 0;
        });
    }

    function setup(editor) {
        editor.addEventListener('paste', function(evt) {
            var files = images(evt.clipboardData && evt.clipboardData.files);
            if (!files.length) {
                return;
            }
            evt.preventDefault();
            files.forEach(function(f) { upload(editor, f); });
        });

        editor.addEventListener('dragover', function(evt) {
            if (evt.dataTransfer && Array.prototype.indexOf.call(evt.dataTransfer.types, 'Files') >= 0) {
                evt.preventDefault();
                editor.classList.add('drop-target');
            }
        });
        editor.addEventListener('dragleave', function() {
            editor.classList.remove('drop-target');
        });
        editor.addEventListener('drop', function(evt) {
            editor.classList.remove('drop-target');
            var files = images(evt.dataTransfer && evt.dataTransfer.files);
            if (!files.length) {
                return;
            }
            evt.preventDefault();
            editor.focus();
            files.forEach(function(f) { upload(editor, f); });
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        if (!window.messages || !messages.upload_url) {
            return;
        }
        var editors = document.querySelectorAll('textarea#editor');
        for (var i = 0; i < editors.length; i++) {
            setup(editors[i]);
        }
    });
})();
