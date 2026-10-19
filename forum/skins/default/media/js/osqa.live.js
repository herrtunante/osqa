/*
 * The live question page (node/live_updates.html, forum/utils/live.py).
 *
 * Every 20 seconds while the tab is visible the page asks
 * GET /questions/<id>/updates/?since=<token> with the ETag of the last
 * answer (a 304 costs the server almost nothing) and compares the snapshot
 * with the page:
 *
 * - vote counts and accepted marks change quietly;
 * - new and edited comments under the shown posts are inserted at once,
 *   with a short highlight;
 * - new answers and edited posts are counted in a polite banner
 *   ("2 new answers · Show"); Show inserts them (rendered by the server
 *   for this visitor, with the page's templates) and moves the focus to
 *   the first one;
 * - someone typing an answer is told when a new answer comes in, with a
 *   link to it; the draft is never touched.
 *
 * Polling stops while the tab is hidden and after 30 minutes without any
 * activity (both resume), backs off on errors and stops for good when the
 * question is gone (404). window.osqaLiveInterval (seconds) overrides the
 * interval (browser tests).
 */
(function () {
    var root = document.getElementById('live-updates');
    if (!root || !window.fetch || !window.Promise) return;

    var url = root.getAttribute('data-url');
    var since = root.getAttribute('data-since');
    var texts = {};
    try { texts = JSON.parse(root.getAttribute('data-texts') || '{}'); } catch (e) { /* keep the defaults */ }
    var latestFirst = root.getAttribute('data-latest-first') === '1';
    var interval = (Number(window.osqaLiveInterval) || Number(root.getAttribute('data-interval')) || 20) * 1000;
    var MAX_DELAY = 5 * 60 * 1000;
    var IDLE_LIMIT = 30 * 60 * 1000;

    var banner = document.getElementById('live-banner');
    var status = document.getElementById('live-status');
    var answersBox = document.getElementById('live-answers');

    var etag = null, timer = null, failures = 0, busy = false, dead = false, idle = false;
    var lastActivity = Date.now();
    var pendingAnswers = {}, pendingEdits = {}, fetchingComments = {};

    function text(name, fallback) {
        return texts[name] || fallback;
    }

    function plural(name, count, fallback) {
        var forms = texts[name] || [fallback, fallback];
        return (count === 1 ? forms[0] : forms[1]).replace('%(count)s', count);
    }

    function keys(obj) {
        return Object.keys(obj);
    }

    function announce(message) {
        if (!status) return;
        status.textContent = '';
        setTimeout(function () { status.textContent = message; }, 50);
    }

    function fragment(html) {
        // A <template> never runs scripts in the HTML it parses.
        var template = document.createElement('template');
        template.innerHTML = html;
        return template.content;
    }

    function enhance(el) {
        if (window.osqaInitPostWidgets) window.osqaInitPostWidgets(el);
        if (window.osqaCopyButtons) window.osqaCopyButtons();
        if (window.osqaHighlight) window.osqaHighlight(el);
        if (window.osqaAvatars) window.osqaAvatars.scan(el);
    }

    // -- polling -----------------------------------------------------------

    function schedule(delay) {
        clearTimeout(timer);
        timer = setTimeout(poll, delay);
    }

    function nextDelay() {
        return failures ? Math.min(interval * Math.pow(2, failures), MAX_DELAY) : interval;
    }

    function poll() {
        timer = null;
        if (dead || busy || document.hidden) return;  // visibilitychange starts again
        if (Date.now() - lastActivity > IDLE_LIMIT) {
            idle = true;  // the next sign of life starts again
            return;
        }
        busy = true;
        var headers = {'Accept': 'application/json'};
        if (etag) headers['If-None-Match'] = etag;
        fetch(url + '?since=' + encodeURIComponent(since), {headers: headers, credentials: 'same-origin', cache: 'no-store'})
            .then(function (response) {
                if (response.headers.get('ETag')) etag = response.headers.get('ETag');
                if (response.status === 304) return null;
                if (response.status === 404 || response.status === 400 || response.status === 403) {
                    dead = true;
                    return null;
                }
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.json();
            })
            .then(function (data) {
                failures = 0;
                if (data) apply(data);
            }, function () {
                failures++;
            })
            .then(function () {
                busy = false;
                if (!dead && !document.hidden) schedule(nextDelay());
            });
    }

    function wake() {
        lastActivity = Date.now();
        if (idle) {
            idle = false;
            schedule(0);
        }
    }

    ['keydown', 'pointerdown', 'pointermove', 'scroll', 'touchstart'].forEach(function (type) {
        window.addEventListener(type, wake, {passive: true});
    });

    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            clearTimeout(timer);
            timer = null;
        } else {
            lastActivity = Date.now();
            idle = false;
            if (!busy) schedule(0);
        }
    });

    // -- applying a snapshot ---------------------------------------------------

    function setScore(id, score, isComment) {
        var board = document.getElementById('post-' + id + '-score');
        if (!board) return;
        var value = isComment && !score ? '' : String(score);
        if (board.textContent.trim() === value) return;
        // Other people's votes change the count without being read out.
        var live = board.getAttribute('aria-live');
        if (live) board.setAttribute('aria-live', 'off');
        board.textContent = value;
        if (live) setTimeout(function () { board.setAttribute('aria-live', live); }, 100);
    }

    function setAccepted(el, id, accepted) {
        if (el.classList.contains('accepted-answer') === accepted) return;
        var commands = window.response_commands;
        if (commands && commands.mark_accepted) {
            commands[accepted ? 'mark_accepted' : 'unmark_accepted'](id);
        } else {
            el.classList.toggle('accepted-answer', accepted);
        }
    }

    function edited(el, rev) {
        var known = el.getAttribute('data-rev');
        if (!known) {
            if (rev) el.setAttribute('data-rev', rev);
            return false;
        }
        return !!rev && String(rev) !== known;
    }

    function apply(data) {
        var question = data.question;
        var questionEl = document.getElementById('question-table');
        if (question && questionEl) {
            setScore(question.id, question.score);
            if (edited(questionEl, question.rev)) pendingEdits[question.id] = true;
            else delete pendingEdits[question.id];
        }

        var listed = {};
        (data.answers || []).forEach(function (answer) {
            listed[answer.id] = true;
            var el = document.getElementById('answer-container-' + answer.id);
            if (el) {
                delete pendingAnswers[answer.id];
                setScore(answer.id, answer.score);
                setAccepted(el, answer.id, answer.accepted);
                if (edited(el, answer.rev)) pendingEdits[answer.id] = true;
                else delete pendingEdits[answer.id];
            } else if (answer['new']) {
                pendingAnswers[answer.id] = true;
            }
        });
        keys(pendingAnswers).forEach(function (id) {
            if (!listed[id]) delete pendingAnswers[id];  // deleted again meanwhile
        });

        var comments = [];
        (data.comments || []).forEach(function (comment) {
            var el = document.getElementById('comment-' + comment.id);
            if (el) {
                setScore(comment.id, comment.score, true);
                // Not while it is being edited here (its form hides it).
                if (edited(el, comment.rev) && el.offsetParent !== null) comments.push(comment.id);
            } else if (document.getElementById('comments-container-' + comment.parent)) {
                comments.push(comment.id);
            }
        });
        comments = comments.filter(function (id) { return !fetchingComments[id]; });
        if (comments.length) insertComments(comments);

        showBanner();
        warnWriter();
    }

    function fetchPosts(ids) {
        return fetch(url + '?posts=' + ids.join(','), {
            headers: {'Accept': 'application/json'}, credentials: 'same-origin', cache: 'no-store'
        }).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        });
    }

    function insertComments(ids) {
        ids.forEach(function (id) { fetchingComments[id] = true; });
        fetchPosts(ids).then(function (data) {
            var added = 0;
            data.posts.forEach(function (post) {
                if (post.type !== 'comment') return;
                var parsed = fragment(post.html);
                var fresh = parsed.querySelector('.comment');
                if (!fresh) return;
                fresh.classList.add('live-new');
                var old = document.getElementById('comment-' + post.id);
                if (old) {
                    old.replaceWith(fresh);
                } else {
                    var container = document.getElementById('comments-container-' + post.parent);
                    if (!container) return;
                    if (latestFirst) container.insertBefore(parsed, container.firstChild);
                    else container.appendChild(parsed);
                    added++;
                }
                enhance(fresh);
            });
            if (added) announce(plural('comments', added, '%(count)s new comments'));
        }, function () { /* tried again on the next change */ }).then(function () {
            ids.forEach(function (id) { delete fetchingComments[id]; });
        });
    }

    // -- banner and Show -------------------------------------------------------

    function showBanner() {
        if (!banner) return;
        var answers = keys(pendingAnswers).length, edits = keys(pendingEdits).length;
        var parts = [];
        if (answers) parts.push(plural('answers', answers, '%(count)s new answers'));
        if (edits) parts.push(plural('edits', edits, '%(count)s edited posts'));
        var message = parts.join(', ');
        if (banner.getAttribute('data-message') === message) return;
        banner.setAttribute('data-message', message);
        banner.textContent = '';
        if (!message) return;
        var inner = document.createElement('div');
        inner.className = 'live-banner-inner';
        var label = document.createElement('span');
        label.className = 'live-banner-text';
        label.textContent = message;
        var dot = document.createElement('span');
        dot.className = 'live-banner-dot';
        dot.setAttribute('aria-hidden', 'true');
        dot.textContent = '·';
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'live-show';
        button.textContent = text('show', 'Show');
        button.setAttribute('title', text('show_label', 'Show the new and edited posts'));
        button.addEventListener('click', show);
        inner.appendChild(label);
        inner.appendChild(dot);
        inner.appendChild(button);
        banner.appendChild(inner);
    }

    function replacePart(target, source, selector) {
        var to = target.querySelector(selector), from = source.querySelector(selector);
        if (to && from) to.innerHTML = from.innerHTML;
    }

    function show() {
        var ids = keys(pendingAnswers).concat(keys(pendingEdits));
        if (!ids.length) return Promise.resolve(null);
        return fetchPosts(ids).then(function (data) {
            var first = null;
            data.posts.forEach(function (post) {
                if (post.type !== 'answer') return;
                delete pendingAnswers[post.id];
                delete pendingEdits[post.id];
                var parsed = fragment(post.html);
                var fresh = parsed.querySelector('article');
                if (!fresh) return;
                var old = document.getElementById('answer-container-' + post.id);
                if (old) {
                    // An open comment form keeps what is typed in it: only the post itself changes.
                    replacePart(old, fresh, '.answer-body');
                    replacePart(old, fresh, '.post-update-info-container');
                    old.setAttribute('data-rev', post.rev || '');
                    old.classList.add('live-new');
                    enhance(old);
                    first = first || old;
                } else if (answersBox) {
                    fresh.classList.add('live-new');
                    fresh.setAttribute('tabindex', '-1');
                    answersBox.appendChild(parsed);
                    enhance(fresh);
                    first = first || fresh;
                }
            });
            var question = data.question;
            var questionEl = document.getElementById('question-table');
            if (question && questionEl) {
                delete pendingEdits[question.id];
                var body = questionEl.querySelector('.question-body');
                if (body) body.innerHTML = question.body;
                var tags = document.getElementById('question-tags');
                if (tags) tags.innerHTML = question.tags;
                var info = questionEl.querySelector('.post-update-info-container');
                if (info) info.innerHTML = question.info;
                var title = document.querySelector('.question-header h1 a');
                if (title) title.textContent = question.title;
                questionEl.setAttribute('data-rev', question.rev || '');
                questionEl.classList.add('live-new');
                enhance(questionEl);
                first = first || questionEl;
            }
            var heading = document.querySelector('.answers-count');
            if (heading && data.answers_heading) heading.textContent = data.answers_heading;
            // Answers that are no longer there (deleted meanwhile) leave the banner too.
            ids.forEach(function (id) { delete pendingAnswers[id]; delete pendingEdits[id]; });
            showBanner();
            removeWriterNotice();
            if (first) {
                if (!first.hasAttribute('tabindex')) first.setAttribute('tabindex', '-1');
                first.focus({preventScroll: true});
                first.scrollIntoView({block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
            }
            announce(text('shown', 'The new posts are shown.'));
            return first;
        }, function () { /* the banner stays: try again */ });
    }

    // -- someone is writing an answer -------------------------------------------

    function removeWriterNotice() {
        var notice = document.getElementById('live-writing');
        if (notice) notice.remove();
    }

    function warnWriter() {
        var editor = document.getElementById('editor');
        if (!editor || !editor.value.trim() || !keys(pendingAnswers).length || document.getElementById('live-writing')) return;
        var form = document.getElementById('fmanswer');
        var submit = form && form.querySelector('.answer-submit');
        if (!submit) return;
        var notice = document.createElement('p');
        notice.id = 'live-writing';
        notice.className = 'live-writing';
        notice.setAttribute('role', 'status');
        notice.appendChild(document.createTextNode(text('writing', 'A new answer was posted while you were writing.') + ' '));
        var link = document.createElement('a');
        link.href = '#live-answers';
        link.textContent = text('writing_link', 'Read it');
        link.addEventListener('click', function (evt) {
            evt.preventDefault();
            show();
        });
        notice.appendChild(link);
        submit.parentNode.insertBefore(notice, submit);
    }

    if (!document.hidden) schedule(interval);
})();
