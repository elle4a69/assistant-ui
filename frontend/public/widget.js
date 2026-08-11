if (typeof SimplybookWidget !== 'undefined') {
    console.error('%c[SimplyBook widget] widget.js is loaded more than once on this page. Remove the duplicate <script> tag to avoid unexpected issues.', 'color: red; font-weight: bold');
} else {

var SimplybookWidget = function (options) {
    this.options = {
        // common options
        'container_id': null,
        'widget_type': 'iframe',
        'widget_user_class': '',
        'theme': 'default',
        'theme_settings': {},
        'app_config': {},
        'timeline': null,
        'datepicker': null,
        'url': '',
        // iframe widget options
        'iframe_postpone': false,
        // button widget options
        'button_position': 'right',
        'button_position_offset': 'auto',
        'button_background_color': '#06acee',
        'button_text_color': '#ffffff',
        'button_title': 'Book Now',
        'button_preload': true,
        'button_custom_id': null,
        'navigate': 'book'
    };
    this.modalsPositions = {};
    this._modalOpenCount = 0;
    this._baseFrameHeight = 0;
    this.name = 'widget_' + Math.random();
    this.frame = null;

    if (SimplybookWidget._instanceCreated) {
        console.error('%c[SimplyBook widget] Multiple SimplybookWidget instances detected on this page. This may cause unexpected behavior.', 'color: red; font-weight: bold');
    }
    SimplybookWidget._instanceCreated = true;

    for (var name in options) {
        if (!options.hasOwnProperty(name)) {
            continue;
        }
        this.options[name] = options[name];
    }

    this.init();
};

SimplybookWidget.prototype.init = function () {
    document.cookie = 'sb_widget=1';
    this._pendingWidgetCallback = null;
    this._checkWidgetCallback();

    if (!this.options.postpone) {
        switch (this.options.widget_type) {
            case 'iframe':
                this.displayIframe();
                break;

            case 'reviews':
                this.options.navigate = 'reviews';
                this.displayIframe();
                break;

            case 'button':
                this.addButton();
                break;

            case 'contact-button':
                this.addContactButton();
                break;

            case 'gift-card':
                this.options.navigate = 'gift-card';
                this.displayIframe();
                break;

            case 'packages':
                this.options.navigate = 'packages';
                this.displayIframe();
                break;

            case 'membership':
                this.options.navigate = 'membership';
                this.displayIframe();
                break;
        }
    }
};

SimplybookWidget.prototype.onReceiveMessage = function (message) {
    if (typeof message.data === 'object') {
        if (!this.frame || message.source !== this.frame.contentWindow) {
            return;
        }
        switch (message.data.event) {
            case 'appReady':
                if (this._pendingWidgetCallback) {
                    this.options.navigate = 'invoice/pay/' + this._pendingWidgetCallback + '/return/1';
                    this._pendingWidgetCallback = null;
                }
                this.setSettings(message.data);
                break;
            case 'updateWidgetSize':
                this.updateWidgetSize(message.data);
                break;
            case 'closeWidget':
                this.closePopup();
                break;
            case 'modalShown':
                if (this.options.widget_type === 'iframe' || this.options.widget_type === 'gift-card' || this.options.widget_type === 'packages' || this.options.widget_type === 'membership') {
                    this.changeModalPosition(message.data.listener_id);
                }
                break;
            case 'modalShow':
                if (this.options.widget_type === 'iframe' || this.options.widget_type === 'gift-card' || this.options.widget_type === 'packages' || this.options.widget_type === 'membership') {
                    this.saveModalPosition(message.data.listener_id);
                }
                break;
            case 'modalHidden':
                if (this.options.widget_type === 'iframe' || this.options.widget_type === 'gift-card' || this.options.widget_type === 'packages' || this.options.widget_type === 'membership') {
                    this.updatePositionAfterModal(message.data.listener_id);
                }
                break;
            case 'stepChanged':
            case 'scrollTo':
                if (this.options.widget_type === 'iframe' || this.options.widget_type === 'gift-card' || this.options.widget_type === 'packages' || this.options.widget_type === 'membership') {
                    this.scrollToContent(message.data.content_position);
                }
                break;
            case 'expandHeight':
                if (this.options.widget_type === 'iframe' || this.options.widget_type === 'gift-card' || this.options.widget_type === 'packages' || this.options.widget_type === 'membership') {
                    this._onExpandHeight(message.data.height);
                }
                break;
            case 'collapseHeight':
                if (this.options.widget_type === 'iframe' || this.options.widget_type === 'gift-card' || this.options.widget_type === 'packages' || this.options.widget_type === 'membership') {
                    this._onCollapseHeight();
                }
                break;
        }
    }
};

SimplybookWidget.prototype._checkWidgetCallback = function () {
    var url = new URL(window.location.href);
    var sbwc = url.searchParams.get('sbwc');
    if (!sbwc) {
        return;
    }
    url.searchParams.delete('sbwc');
    window.history.replaceState(null, '', url.toString());
    this._pendingWidgetCallback = sbwc;
};

SimplybookWidget.prototype.setSettings = function () {
    var win = this.frame.contentWindow || this.frame;

    var postData = {
        'update_config': true,
        'update_theme_vars': true,
        'rerender': this.options.navigate === null,
        'theme_vars': this.options.theme_settings,
        'config_vars': this.options.app_config,
        'hostname': window.location.hostname,
        'url': window.location.href,
    };

    if(this.options.navigate){
        postData['navigate'] = this.options.navigate;
        this.options.navigate = null;
    }

    if(this.options.reviews_count || this.options.hide_add_reviews){
        postData['reviews_vars'] = {
            "reviews_count": this.options.reviews_count,
            "hide_add_reviews": parseInt(this.options.hide_add_reviews)
        };
    }

    win.postMessage(postData, '*');
};

SimplybookWidget.prototype.navigate = function (to) {
    var win = this.frame.contentWindow || this.frame;

    win.postMessage({
        'navigate': to
    }, '*');
};

SimplybookWidget.prototype.scrollToContent = function (contentPos) {
    var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    var iframePosition = this.frame.getBoundingClientRect();

    var top = scrollTop + iframePosition.top + contentPos;

    if (scrollTop > top) {
        window.scrollTo(0, top);
    }
};

SimplybookWidget.prototype.changeModalPosition = function (listenerId) {
    if (this.modalsPositions[listenerId]) {
        window.scrollTo(0, this.modalsPositions[listenerId]);
    }

    var win = this.frame.contentWindow || this.frame;

    var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    var iframePosition = this.frame.getBoundingClientRect();

    var top = scrollTop + iframePosition.top;
    var scrollTo = scrollTop - top;

    win.postMessage({
        'update_modal_position': true,
        'listener_id': listenerId,
        'top': Math.max(scrollTo, 0)
    }, '*');
};

SimplybookWidget.prototype.saveModalPosition = function (listenerId) {
    var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    this.modalsPositions[listenerId] = scrollTop;

    if (this._modalOpenCount === 0) {
        this._baseFrameHeight = parseInt(this.frame.height) || 0;
    }
    this._modalOpenCount++;
    this._expandFrameForModal();
};

SimplybookWidget.prototype.updatePositionAfterModal = function (listenerId) {
    if (this.modalsPositions[listenerId]) {
        window.scrollTo(0, this.modalsPositions[listenerId]);
    }
    this._modalOpenCount = Math.max(0, this._modalOpenCount - 1);
    if (this._modalOpenCount === 0 && this._baseFrameHeight > 0) {
        this.frame.height = this._baseFrameHeight;
    }
};

SimplybookWidget.prototype._expandFrameForModal = function () {
    var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    var minHeight = Math.round(viewportHeight * 0.9);
    var currentHeight = parseInt(this.frame.height) || 0;
    if (currentHeight < minHeight) {
        this.frame.height = minHeight;
    }
};

// Called when an absolutely-positioned overlay (e.g. full-info panel) opens inside the iframe.
// The inner page sends: postMessage({ event: 'expandHeight', height: <requiredHeight> }, '*')
SimplybookWidget.prototype._onExpandHeight = function (height) {
    if (this._modalOpenCount === 0) {
        this._baseFrameHeight = parseInt(this.frame.height) || 0;
    }
    this._modalOpenCount++;
    var requiredHeight = parseInt(height) || 0;
    var currentHeight = parseInt(this.frame.height) || 0;
    if (requiredHeight > currentHeight) {
        this.frame.height = requiredHeight;
    } else {
        this._expandFrameForModal();
    }
};

// Called when the overlay closes.
// The inner page sends: postMessage({ event: 'collapseHeight' }, '*')
SimplybookWidget.prototype._onCollapseHeight = function () {
    this._modalOpenCount = Math.max(0, this._modalOpenCount - 1);
    if (this._modalOpenCount === 0 && this._baseFrameHeight > 0) {
        this.frame.height = this._baseFrameHeight;
    }
};

SimplybookWidget.prototype.getUrl = function () {
    var widget_type = this.options.widget_type;
    if (widget_type == 'contact-button') {
        widget_type = 'button';
    }

    var getCookie = function(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
    };

    var url = this.options.url + '/v2/?widget-type=' + encodeURIComponent(widget_type);
    if (this.options.theme) {
        url += '&theme=' + encodeURIComponent(this.options.theme);
    }
    if (this.options.theme_id) {
        url += '&theme_id=' + this.options.theme_id;
    }
    if (this.options.timeline) {
        url += '&timeline=' + this.options.timeline;
    }
    if (this.options.datepicker) {
        url += '&datepicker=' + this.options.datepicker;
    }
    if (this.options.is_rtl) {
        url += '&is_rtl=1';
    }
    if (this.options.widget_user_class) {
        url += '&class=' + this.options.widget_user_class;
    }

    if(getCookie('_ga')){
        url += '&_ga=' + getCookie('_ga');
    }
    url += '&host_url=' + encodeURIComponent(window.location.href);
    return url;
};

SimplybookWidget.prototype.subscribeMessages = function () {
    var instance = this;
    window.addEventListener("message", function (data) {
        instance.onReceiveMessage(data);
    }, false);
};

// iframe widget methods
SimplybookWidget.prototype.updateWidgetSize = function (data) {
    if (this.options.widget_type !== 'iframe' && this.options.widget_type !== 'gift-card' && this.options.widget_type !== 'packages' && this.options.widget_type !== 'membership' && this.options.widget_type !== 'reviews') {
        return;
    }
    // if(['iframe', 'reviews', 'gift-card', 'package'].indexOf(this.options.widget_type) >= 0){
    //     return;
    // }
    var newHeight = parseInt(data.height) || 0;
    if (this._modalOpenCount > 0) {
        // Modal is open: only grow the iframe, never shrink it
        if (newHeight > (parseInt(this.frame.height) || 0)) {
            this.frame.height = newHeight;
        }
    } else {
        this.frame.height = newHeight;
        this._baseFrameHeight = newHeight;
    }
};

SimplybookWidget.prototype.displayIframe = function () {
    var containerId = this.options.container_id;
    var iframeHtml =  '<iframe scrolling="no" class="sb-widget-iframe" width="100%" height="800" border="0" frameborder="0" src="' + this.getUrl() + '" name="' + this.name + '" id="' + this.name + '" title="Booking widget"></iframe>';

    if(containerId){
        if(typeof containerId === 'object'){
            this.container = containerId;
        }else{
            this.container = document.getElementById(containerId);
            if(!this.container){
                this.container = document.createElement('div');
                this.container.id = containerId;
                document.body.appendChild(this.container);
            }
        }
        this.container.innerHTML = iframeHtml;
    } else {
        document.write(iframeHtml);
    }
    this.frame = document.getElementById(this.name);
    this.subscribeMessages();
};

// button widget methods
SimplybookWidget.prototype.addButton = function () {
    this.addButtonWidgetStyles();

    var btn;
    if (this.options.button_custom_id) {
        btn = document.getElementById(this.options.button_custom_id);
        this._makeElementAccessible(btn);
    } else {
        btn = this.getButtonNode();
    }

    var instance = this;
    btn.addEventListener('click', function () {
        instance._triggerElement = btn;
        instance.showPopupFrame('book');
    });

    if (!this.options.button_custom_id) {
        if (!document.body) {
            document.getElementsByTagName('html')[0].appendChild(document.createElement('body'))
        }
        document.body.appendChild(btn);
    }
};

SimplybookWidget.prototype.addContactButton = function () {
    this.addButtonWidgetStyles();

    var btn;
    if (this.options.button_custom_id) {
        btn = document.getElementById(this.options.button_custom_id);
        this._makeElementAccessible(btn);
    } else {
        btn = this.getButtonNode();
    }

    var instance = this;
    btn.addEventListener('click', function () {
        instance._triggerElement = btn;
        instance.showPopupFrame('contact-widget');
    });

    if (!this.options.button_custom_id) {
        if (!document.body) {
            document.getElementsByTagName('html')[0].appendChild(document.createElement('body'))
        }
        document.body.appendChild(btn);
    }
};

SimplybookWidget.prototype.getButtonNode = function () {
    // Use native <button> so it is focusable and keyboard-activatable out of the box
    this.btn = document.createElement('button');
    this.btn.type = 'button';
    this.btn.setAttribute('aria-haspopup', 'dialog');
    this.btn.setAttribute('aria-expanded', 'false');

    this.btnLabel = document.createElement('span');
    this.btnLabel.innerText = this.options.button_title;

    this.btn.appendChild(this.btnLabel);

    var instance = this;
    this.btn.addEventListener('click', function () {
        instance._triggerElement = instance.btn;
    });

    this.updateButtonStyles();

    return this.btn;
};

/**
 * Makes a custom-provided element keyboard-accessible when it is not a native
 * interactive element (e.g. a <div> or <span> used as a button).
 */
SimplybookWidget.prototype._makeElementAccessible = function (el) {
    if (!el) { return; }
    var tag = el.tagName.toLowerCase();
    var nativelyFocusable = ['button', 'a', 'input', 'select', 'textarea'].indexOf(tag) !== -1;

    if (!nativelyFocusable) {
        if (!el.hasAttribute('tabindex')) {
            el.setAttribute('tabindex', '0');
        }
        if (!el.getAttribute('role')) {
            el.setAttribute('role', 'button');
        }
        // Enter / Space activate the element (browsers only do this for native <button>)
        el.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ' || e.keyCode === 13 || e.keyCode === 32) {
                e.preventDefault();
                el.click();
            }
        });
    }

    el.setAttribute('aria-haspopup', 'dialog');
    el.setAttribute('aria-expanded', 'false');
};

SimplybookWidget.prototype.updateButtonStyles = function (options) {
    if (!this.btn) {
        return;
    }
    if (options) {
        this.options.button_position = options.button_position;
        this.options.button_background_color = options.button_background_color;
        this.options.button_text_color = options.button_text_color;
        this.options.button_position_offset = options.button_position_offset;
        this.options.button_title = options.button_title;
    }

    this.btn.className = 'simplybook-widget-button ' + this.options.button_position;
    this.btn.style.backgroundColor = this.options.button_background_color;
    this.btn.style.color = this.options.button_text_color;
    if (this.options.button_position === 'top' || this.options.button_position === 'bottom') {
        this.btn.style.right = this.options.button_position_offset;
        this.btn.style.bottom = '';
    } else {
        this.btn.style.bottom = this.options.button_position_offset;
        this.btn.style.right = '';
    }
    this.btnLabel.innerText = this.options.button_title;
    this._injectDynamicStyles();
};

SimplybookWidget.prototype._darkenColor = function (hex, percent) {
    hex = hex.trim();
    if (/^#[0-9a-fA-F]{3}$/.test(hex)) {
        hex = '#' + hex[1] + hex[1] + hex[2] + hex[2] + hex[3] + hex[3];
    }
    if (!/^#[0-9a-fA-F]{6}$/.test(hex)) {
        return hex;
    }
    var factor = 1 - percent / 100;
    var r = Math.max(0, Math.floor(parseInt(hex.slice(1, 3), 16) * factor));
    var g = Math.max(0, Math.floor(parseInt(hex.slice(3, 5), 16) * factor));
    var b = Math.max(0, Math.floor(parseInt(hex.slice(5, 7), 16) * factor));
    return '#' + ('0' + r.toString(16)).slice(-2) + ('0' + g.toString(16)).slice(-2) + ('0' + b.toString(16)).slice(-2);
};

SimplybookWidget.prototype._injectDynamicStyles = function () {
    var styleId = 'sb-widget-btn-styles';
    var existing = document.getElementById(styleId);
    if (existing) {
        existing.parentNode.removeChild(existing);
    }
    var color = this.options.button_background_color || '#06acee';
    var hoverColor = this._darkenColor(color, 5);
    var style = document.createElement('style');
    style.id = styleId;
    style.textContent = [
        '.simplybook-widget-button:hover { background-color: ' + hoverColor + ' !important; }',
        '.simplybook-widget-button:focus-visible { outline: 2px solid ' + color + '; outline-offset: 2px; border-radius: 4px; }'
    ].join('\n');
    document.head.appendChild(style);
};

SimplybookWidget.prototype.resetWidget = function (options) {
    this.options = options;

    this.updateButtonStyles(options);

    this.container = null;
    this.frame = null;
};

SimplybookWidget.prototype.showPopupFrame = function (navigateTo) {
    if (navigateTo === undefined) {
        navigateTo = 'book';
    }

    if (!this.container) {
        this.container = document.createElement('div');
        this.container.className = 'simplybook-widget-container active';
        // Semantics: dialog landmark + focus target
        this.container.setAttribute('role', 'dialog');
        this.container.setAttribute('aria-modal', 'true');
        this.container.setAttribute('aria-label', this.options.button_title || 'Booking widget');
        this.container.setAttribute('tabindex', '-1');

        this.options.navigate = navigateTo;
        this.container.appendChild(this.getIframeNode());

        document.body.appendChild(this.container);
    } else {
        this.container.className = 'simplybook-widget-container active';
        this.navigate(navigateTo);
    }

    // Update trigger aria-expanded
    if (this._triggerElement) {
        this._triggerElement.setAttribute('aria-expanded', 'true');
    }

    this.showOverlay();

    // Move focus into the dialog so SR announces it
    var container = this.container;
    setTimeout(function () { container.focus(); }, 50);
};

SimplybookWidget.prototype.closePopup = function () {
    this.hideOverlay();
    this.container.className = 'simplybook-widget-container';

    // Update trigger aria-expanded and return focus
    if (this._triggerElement) {
        this._triggerElement.setAttribute('aria-expanded', 'false');
        this._triggerElement.focus();
    }
};

SimplybookWidget.prototype.showOverlay = function () {
    if (!this.overlay) {
        this.overlay = document.createElement('div');
        this.overlay.className = 'simplybook-widget-overlay';
        this.overlay.setAttribute('aria-hidden', 'true');

        var instance = this;
        this.overlay.addEventListener('click', function () {
            instance.closePopup();
        });

        // ESC closes the popup (WCAG 2.1.2 — keyboard dismissal)
        this._escHandler = function (e) {
            if (e.key === 'Escape' || e.keyCode === 27) {
                instance.closePopup();
            }
        };
        document.addEventListener('keydown', this._escHandler);
    }

    document.body.appendChild(this.overlay);

    var instance = this;
    // to show animated appear
    setTimeout(function () {
        instance.overlay.className = instance.overlay.className + ' active';
    }, 10);
};

SimplybookWidget.prototype.hideOverlay = function () {
    if (this.overlay) {
        this.overlay.className = 'simplybook-widget-overlay';

        if (this._escHandler) {
            document.removeEventListener('keydown', this._escHandler);
            this._escHandler = null;
        }

        var instance = this;
        setTimeout(function () {
            document.body.removeChild(instance.overlay);
        }, 300);
    }
};

SimplybookWidget.prototype.addButtonWidgetStyles = function () {
    var link = document.createElement('link');
    link.setAttribute('rel', 'stylesheet');
    link.setAttribute('type', 'text/css');
    link.setAttribute('href', this.options.url + '/v2/widget/widget.css');

    document.getElementsByTagName('head')[0].appendChild(link);
};

SimplybookWidget.prototype.getIframeNode = function () {
    if (!this.frame) {
        this.frame = document.createElement('iframe');
        this.frame.border = '0';
        this.frame.frameBorder = '0';
        if (this.options.widget_type == 'iframe') {
            this.frame.scrolling = 'no';
        }
        this.frame.name = this.name;
        this.frame.id = this.name;
        this.frame.title = this.options.button_title || 'Booking widget';
        this.frame.src = this.getUrl();

        this.subscribeMessages();
    }

    return this.frame;
};

} // end SimplybookWidget guard
