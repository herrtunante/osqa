/*
 * Passkeys (WebAuthn), forum/views/security.py:
 *
 * [data-passkey-signin]    "Sign in with a passkey" (auth/passkey_signin.html):
 *                          shown when the browser has WebAuthn; where the browser
 *                          supports it, passkeys are also offered in the username
 *                          field's autofill (conditional mediation).
 * [data-passkey-register]  the "Add a passkey" form of the security page; when the
 *                          options answer {redirect} (no recent sign-in), it goes
 *                          to "Confirm it's you" first.
 * The "Confirm it's you" page (auth/confirm_identity.html) uses [data-passkey-signin]
 * with its own URLs.
 *
 * Both POST JSON (with the X-CSRFToken header) to the URLs in their
 * data-options-url / data-verify-url attributes: first for the options, then
 * with what the browser answered; the reply is {redirect} or {error}.
 */
(function () {
    'use strict';

    var supported = !!(window.PublicKeyCredential && navigator.credentials && window.fetch);

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function post(url, data) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrfToken()},
            body: JSON.stringify(data || {})
        }).then(function (response) {
            return response.json().catch(function () {
                return {error: response.statusText || 'Error'};
            });
        });
    }

    // -- base64url <-> ArrayBuffer (for browsers without the JSON helpers) --

    function toBuffer(value) {
        var text = value.replace(/-/g, '+').replace(/_/g, '/');
        text += '===='.slice(0, (4 - text.length % 4) % 4);
        var binary = atob(text), bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    }

    function toBase64url(buffer) {
        var bytes = new Uint8Array(buffer), binary = '';
        for (var i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function creationOptions(json) {
        if (PublicKeyCredential.parseCreationOptionsFromJSON) {
            return PublicKeyCredential.parseCreationOptionsFromJSON(json);
        }
        var options = Object.assign({}, json);
        options.challenge = toBuffer(json.challenge);
        options.user = Object.assign({}, json.user, {id: toBuffer(json.user.id)});
        options.excludeCredentials = (json.excludeCredentials || []).map(function (c) {
            return Object.assign({}, c, {id: toBuffer(c.id)});
        });
        return options;
    }

    function requestOptions(json) {
        if (PublicKeyCredential.parseRequestOptionsFromJSON) {
            return PublicKeyCredential.parseRequestOptionsFromJSON(json);
        }
        var options = Object.assign({}, json);
        options.challenge = toBuffer(json.challenge);
        options.allowCredentials = (json.allowCredentials || []).map(function (c) {
            return Object.assign({}, c, {id: toBuffer(c.id)});
        });
        return options;
    }

    function credentialJSON(credential) {
        if (typeof credential.toJSON === 'function') {
            try {
                return credential.toJSON();
            } catch (e) { /* fall through */ }
        }
        var response = credential.response, json = {
            id: credential.id,
            rawId: toBase64url(credential.rawId),
            type: credential.type,
            authenticatorAttachment: credential.authenticatorAttachment || undefined,
            clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
            response: {clientDataJSON: toBase64url(response.clientDataJSON)}
        };
        if (response.attestationObject) {
            json.response.attestationObject = toBase64url(response.attestationObject);
            json.response.transports = response.getTransports ? response.getTransports() : [];
        } else {
            json.response.authenticatorData = toBase64url(response.authenticatorData);
            json.response.signature = toBase64url(response.signature);
            if (response.userHandle) {
                json.response.userHandle = toBase64url(response.userHandle);
            }
        }
        return json;
    }

    function showError(box, message) {
        var error = box.querySelector('[data-passkey-error]');
        if (error) {
            error.textContent = message || '';
            error.hidden = !message;
        }
    }

    // Cancelled by the person (or replaced by another request): no message.
    function quiet(error) {
        return error && (error.name === 'AbortError' || error.name === 'NotAllowedError');
    }

    // -- signing in --

    function setUpSignin(box) {
        var button = box.querySelector('[data-passkey-button]');
        var pending = null;   // the autofill request waiting in the background

        function signIn(conditional) {
            if (pending) {
                pending.abort();
                pending = null;
            }
            var controller = window.AbortController ? new AbortController() : null;
            if (conditional) {
                pending = controller;
            }
            return post(box.getAttribute('data-options-url')).then(function (json) {
                if (json.error) {
                    throw new Error(json.error);
                }
                var request = {publicKey: requestOptions(json)};
                if (controller) {
                    request.signal = controller.signal;
                }
                if (conditional) {
                    request.mediation = 'conditional';
                }
                return navigator.credentials.get(request);
            }).then(function (credential) {
                if (!credential) {
                    return null;
                }
                if (button) {
                    button.disabled = true;
                }
                return post(box.getAttribute('data-verify-url'), {credential: credentialJSON(credential)});
            }).then(function (reply) {
                if (!reply) {
                    return;
                }
                if (reply.redirect) {
                    window.location.assign(reply.redirect);
                } else {
                    if (button) {
                        button.disabled = false;
                    }
                    showError(box, reply.error);
                }
            }).catch(function (error) {
                if (button) {
                    button.disabled = false;
                }
                // The background autofill request was not asked for: it fails silently.
                if (!quiet(error) && !conditional) {
                    showError(box, error.message);
                }
            });
        }

        box.hidden = false;
        if (button) {
            button.addEventListener('click', function () {
                showError(box, '');
                signIn(false);
            });
        }
        // Passkeys in the username field's suggestions, where supported.
        var username = document.querySelector('input[autocomplete~="webauthn"]');
        if (username && PublicKeyCredential.isConditionalMediationAvailable) {
            PublicKeyCredential.isConditionalMediationAvailable().then(function (available) {
                if (available && !pending) {
                    signIn(true);
                }
            }).catch(function () {});
        }
    }

    // -- adding a passkey --

    function setUpRegister(form) {
        form.hidden = false;
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var button = form.querySelector('button[type="submit"]');
            var name = (form.querySelector('input[name="name"]') || {}).value || '';
            showError(form, '');
            button.disabled = true;
            post(form.getAttribute('data-options-url')).then(function (json) {
                if (json.redirect) {
                    // "Confirm it's you" first (forum/utils/sudo.py); the member adds the passkey afterwards.
                    window.location.assign(json.redirect);
                    return null;
                }
                if (json.error) {
                    throw new Error(json.error);
                }
                return navigator.credentials.create({publicKey: creationOptions(json)});
            }).then(function (credential) {
                if (!credential) {
                    return null;
                }
                return post(form.getAttribute('data-verify-url'), {credential: credentialJSON(credential), name: name});
            }).then(function (reply) {
                if (!reply) {
                    return;
                }
                if (reply.redirect) {
                    window.location.assign(reply.redirect);
                } else {
                    button.disabled = false;
                    showError(form, reply.error);
                }
            }).catch(function (error) {
                button.disabled = false;
                if (error && error.name === 'InvalidStateError') {
                    showError(form, form.getAttribute('data-exists-message') || error.message);
                } else if (!quiet(error)) {
                    showError(form, error.message);
                }
            });
        });
    }

    function init() {
        document.querySelectorAll('[data-passkey-signin]').forEach(function (box) {
            if (supported) {
                setUpSignin(box);
            }
        });
        document.querySelectorAll('[data-passkey-register]').forEach(function (form) {
            if (supported) {
                setUpRegister(form);
            } else {
                document.querySelectorAll('[data-passkey-unsupported]').forEach(function (note) {
                    note.hidden = false;
                });
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
