// ====================================================
// Theme
// ====================================================

var themeStorageKey = 'powergslb.theme';

// Resolve and apply the theme synchronously during head parse (before the body paints):
// an explicit stored choice wins, otherwise follow the OS prefers-color-scheme.
(function () {
    var stored = localStorage.getItem(themeStorageKey);
    var theme = stored || (window.matchMedia
    && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
})();

// The toggle is a toolbar button; its sun/moon icon is drawn by CSS (.pg-icon-theme) keyed on data-theme,
// so it switches with the theme without any per-grid glyph bookkeeping.
var themeToolbarItem = function () {
    return {id: 'theme', type: 'button', icon: 'pg-icon-theme', hint: 'Toggle theme'};
};

var toggleTheme = function () {
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem(themeStorageKey, next);
};

var themeToolbarClick = function (event) {
    if (event.target === 'theme') {
        toggleTheme();
    }
};

// ====================================================
// Widget configuration
// ====================================================

var w2uiUrl = '/admin/w2ui';

var gridPopupForm = function (event) {
    switch (event.target) {
        case 'gridDomains':
            openPopupForm(event, 'domain', 400, 210, 'formDomains');
            break;
        case 'gridMonitors':
            openPopupForm(event, 'monitor', 400, 210, 'formMonitors');
            break;
        case 'gridRecords':
            openPopupForm(event, 'record', 400, 485, 'formRecords');
            break;
        case 'gridRoutings':
            openPopupForm(event, 'routing', 400, 210, 'formRoutings');
            break;
        case 'gridTypes':
            openPopupForm(event, 'type', 400, 245, 'formTypes');
            break;
        case 'gridUsers':
            openPopupForm(event, 'user', 400, 245, 'formUsers');
            break;
        case 'gridViews':
            openPopupForm(event, 'view', 400, 210, 'formViews');
            break;
    }
};

var openPopupForm = function (event, record, popup_width, popup_height, form_name) {
    var form_title;
    var form = w2ui[form_name];
    var grid = w2ui[event.target];
    switch (event.type) {
        case 'add':
            form_title = 'PowerGSLB: add ' + record;
            form.clear();
            break;
        case 'dblClick':
            form_title = 'PowerGSLB: edit ' + record;
            form.recid = event.recid;
            break;
        case 'edit':
            form_title = 'PowerGSLB: edit ' + record;
            var sel = grid.getSelection();
            if (sel.length === 1) {
                form.recid = sel[0];
            } else {
                form.clear();
            }
            break;
    }
    w2popup.open({
        title: form_title,
        body: '<div id="' + form_name + '" style="width: 100%; height: 100%;"></div>',
        style: 'padding: 15px 0px 0px 0px',
        width: popup_width,
        height: popup_height,
        onOpen: function (event) {
            event.onComplete = function () {
                $('#w2ui-popup').find('#' + form_name).w2render(form_name);
            }
        }
    })
};

var panelStyle = 'border: 1px solid #dfdfdf; padding: 5px;';

var reloadInterval = 3000;
var reloadIntervalId = 0;

var startAutoReload = function () {
    if (reloadIntervalId === 0) {
        reloadIntervalId = setInterval(function () {
            w2ui.gridStatus.reload();
        }, reloadInterval);
        w2ui.gridStatus.toolbar.check('reload');
        w2ui.gridStatus.reload();
    }
};

var stopAutoReload = function () {
    if (reloadIntervalId !== 0) {
        clearInterval(reloadIntervalId);
        reloadIntervalId = 0;
        w2ui.gridStatus.toolbar.uncheck('reload');
    }
};

var config = {

    // ====================================================
    // Layout
    // ====================================================

    layout: {
        name: 'layout',
        panels: [
            {type: 'left', size: 140, style: panelStyle},
            {type: 'main', style: panelStyle}
        ]
    },

    // ====================================================
    // Sidebar
    // ====================================================

    sidebar: {
        name: 'sidebar',
        nodes: [
            {
                id: 'status', text: 'Status', expanded: true, group: true,
                nodes: [
                    {id: 'gridStatus', text: 'Status', img: 'icon-page', selected: true}
                ]
            },
            {
                id: 'gslb', text: 'GSLB', expanded: true, group: true,
                nodes: [
                    {id: 'gridDomains', text: 'Domains', img: 'icon-page'},
                    {id: 'gridMonitors', text: 'Monitors', img: 'icon-page'},
                    {id: 'gridRecords', text: 'Records', img: 'icon-page'},
                    {id: 'gridRoutings', text: 'Routings', img: 'icon-page'},
                    {id: 'gridTypes', text: 'Types', img: 'icon-page'},
                    {id: 'gridViews', text: 'Views', img: 'icon-page'}
                ]
            },
            {
                id: 'users', text: 'Users', expanded: true, group: true,
                nodes: [
                    {id: 'gridUsers', text: 'Users', img: 'icon-page'}
                ]
            }
        ],
        onClick: function (event) {
            switch (event.target) {
                case 'gridDomains':
                case 'gridMonitors':
                case 'gridRecords':
                case 'gridRoutings':
                case 'gridTypes':
                case 'gridUsers':
                case 'gridViews':
                    stopAutoReload();
                // fall through
                case 'gridStatus':
                    w2ui.layout.content('main', w2ui[event.target]);
                    break;
            }
        }
    },

    // ====================================================
    // Status
    // ====================================================

    gridStatus: {
        name: 'gridStatus',
        postData: {data: 'status'},
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'status', caption: 'Status', size: '55px', resizable: true, sortable: true},
            {field: 'domain', caption: 'Domain', size: '100px', resizable: true, sortable: true},
            {field: 'name', caption: 'Name', size: '100px', resizable: true, sortable: true},
            {field: 'name_type', caption: 'Type', size: '60px', resizable: true, sortable: true},
            {field: 'content', caption: 'Content', size: '510px', resizable: true, sortable: true},
            {field: 'ttl', caption: 'TTL', size: '55px', resizable: true, sortable: true},
            {field: 'disabled', caption: 'Disabled', size: '65px', resizable: true, sortable: true},
            {field: 'weight', caption: 'Weight', size: '55px', resizable: true, sortable: true},
            {field: 'policy', caption: 'Routing', size: '150px', resizable: true, sortable: true},
            {field: 'monitor', caption: 'Monitor', size: '150px', resizable: true, sortable: true},
            {field: 'view', caption: 'View', size: '100px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'status', caption: 'Status', type: 'text'},
            {field: 'domain', caption: 'Domain', type: 'text'},
            {field: 'name', caption: 'Name', type: 'text'},
            {field: 'name_type', caption: 'Type', type: 'text'},
            {field: 'content', caption: 'Content', type: 'text'},
            {field: 'ttl', caption: 'TTL', type: 'int'},
            {field: 'disabled', caption: 'Disabled', type: 'int'},
            {field: 'weight', caption: 'Weight', type: 'int'},
            {field: 'policy', caption: 'Routing', type: 'text'},
            {field: 'monitor', caption: 'Monitor', type: 'text'},
            {field: 'view', caption: 'View', type: 'text'}
        ],
        show: {
            footer: true,
            toolbar: true,
            toolbarReload: true,
            toolbarColumns: true,
            toolbarSearch: true
        },
        sortData: [
            {field: 'status', direction: 'asc'},
            {field: 'recid', direction: 'asc'}
        ],
        toolbar: {
            items: [
                {id: 'break', type: 'break'},
                {
                    id: 'reload', type: 'check', caption: 'Auto', icon: 'w2ui-icon-reload',
                    checked: false, hint: 'Auto reload data in the list'
                },
                {type: 'spacer'},
                themeToolbarItem()
            ],
            onClick: function (event) {
                if (event.target === 'reload') {
                    event.preventDefault();
                    if (reloadIntervalId === 0) {
                        startAutoReload();
                    } else {
                        stopAutoReload();
                    }
                } else if (event.target === 'theme') {
                    toggleTheme();
                }
            }
        },
        onSelect: function (event) {
            event.preventDefault();
        }
    },

    // ====================================================
    // Domains
    // ====================================================

    gridDomains: {
        name: 'gridDomains',
        postData: {data: 'domains'},
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'domain', caption: 'Domain', size: '100px', resizable: true, sortable: true},
            {field: 'description', caption: 'Description', size: '300px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'domain', caption: 'Domain', type: 'text'},
            {field: 'description', caption: 'Description', type: 'text'}
        ]
    },

    formDomains: {
        name: 'formDomains',
        postData: {data: 'domains'},
        fields: [
            {field: 'domain', type: 'text', required: true, html: {caption: 'Domain: '}},
            {field: 'description', type: 'text', required: false, html: {caption: 'Description: '}}
        ]
    },

    // ====================================================
    // Monitors
    // ====================================================

    gridMonitors: {
        name: 'gridMonitors',
        postData: {data: 'monitors'},
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'monitor', caption: 'Monitor', size: '150px', resizable: true, sortable: true},
            {field: 'monitor_json', caption: 'Monitor JSON', size: '750px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'monitor', caption: 'Monitor', type: 'text'},
            {field: 'monitor_json', caption: 'Monitor JSON', type: 'text'}
        ]
    },

    formMonitors: {
        name: 'formMonitors',
        postData: {data: 'monitors'},
        fields: [
            {field: 'monitor', type: 'text', required: true, html: {caption: 'Monitor: '}},
            {field: 'monitor_json', type: 'text', required: true, html: {caption: 'Monitor JSON: '}}
        ]
    },

    // ====================================================
    // Records
    // ====================================================

    gridRecords: {
        name: 'gridRecords',
        postData: {data: 'records'},
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'domain', caption: 'Domain', size: '100px', resizable: true, sortable: true},
            {field: 'name', caption: 'Name', size: '100px', resizable: true, sortable: true},
            {field: 'name_type', caption: 'Type', size: '60px', resizable: true, sortable: true},
            {field: 'content', caption: 'Content', size: '510px', resizable: true, sortable: true},
            {field: 'ttl', caption: 'TTL', size: '55px', resizable: true, sortable: true},
            {field: 'disabled', caption: 'Disabled', size: '65px', resizable: true, sortable: true},
            {field: 'weight', caption: 'Weight', size: '55px', resizable: true, sortable: true},
            {field: 'policy', caption: 'Routing', size: '150px', resizable: true, sortable: true},
            {field: 'monitor', caption: 'Monitor', size: '150px', resizable: true, sortable: true},
            {field: 'view', caption: 'View', size: '100px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'domain', caption: 'Domain', type: 'text'},
            {field: 'name', caption: 'Name', type: 'text'},
            {field: 'name_type', caption: 'Type', type: 'text'},
            {field: 'content', caption: 'Content', type: 'text'},
            {field: 'ttl', caption: 'TTL', type: 'int'},
            {field: 'disabled', caption: 'Disabled', type: 'int'},
            {field: 'weight', caption: 'Weight', type: 'int'},
            {field: 'policy', caption: 'Routing', type: 'text'},
            {field: 'monitor', caption: 'Monitor', type: 'text'},
            {field: 'view', caption: 'View', type: 'text'}
        ]
    },

    formRecords: {
        name: 'formRecords',
        postData: {data: 'records'},
        focus: 1,
        fields: [
            {
                field: 'domain', type: 'combo', required: true, html: {caption: 'Domain: '},
                options: {
                    postData: {'cmd': 'get-items', data: 'domains', field: 'domain'},
                    placeholder: 'Type to search...', match: 'contains', url: w2uiUrl
                }
            },
            {field: 'name', type: 'text', required: true, html: {caption: 'Name: '}},
            {
                field: 'name_type', type: 'combo', required: true, html: {caption: 'Type: '},
                options: {
                    postData: {'cmd': 'get-items', data: 'types', field: 'name_type'},
                    placeholder: 'Type to search...', match: 'contains', url: w2uiUrl
                }
            },
            {field: 'content', type: 'text', required: true, html: {caption: 'Content: '}},
            {
                field: 'ttl', type: 'int', required: true, html: {caption: 'TTL: '},
                options: {autoFormat: false}
            },
            {field: 'disabled', type: 'toggle', required: false, html: {caption: 'Disabled: '}},
            {
                field: 'weight', type: 'int', required: false, html: {caption: 'Weight: '},
                options: {autoFormat: false}
            },
            {
                field: 'policy', type: 'combo', required: true, html: {caption: 'Routing: '},
                options: {
                    postData: {'cmd': 'get-items', data: 'routings', field: 'policy'},
                    placeholder: 'Type to search...', match: 'contains', url: w2uiUrl
                }
            },
            {
                field: 'monitor', type: 'combo', required: true, html: {caption: 'Monitor: '},
                options: {
                    postData: {'cmd': 'get-items', data: 'monitors', field: 'monitor'},
                    placeholder: 'Type to search...', match: 'contains', url: w2uiUrl
                }
            },
            {
                field: 'view', type: 'combo', required: true, html: {caption: 'View: '},
                options: {
                    postData: {'cmd': 'get-items', data: 'views', field: 'view'},
                    placeholder: 'Type to search...', match: 'contains', url: w2uiUrl
                }
            }
        ]
    },

    // ====================================================
    // Routings
    // ====================================================

    gridRoutings: {
        name: 'gridRoutings',
        postData: {data: 'routings'},
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'policy', caption: 'Policy', size: '150px', resizable: true, sortable: true},
            {field: 'policy_json', caption: 'Policy JSON', size: '750px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'policy', caption: 'Policy', type: 'text'},
            {field: 'policy_json', caption: 'Policy JSON', type: 'text'}
        ]
    },

    formRoutings: {
        name: 'formRoutings',
        postData: {data: 'routings'},
        fields: [
            {field: 'policy', type: 'text', required: true, html: {caption: 'Policy: '}},
            {field: 'policy_json', type: 'text', required: true, html: {caption: 'Policy JSON: '}}
        ]
    },

    // ====================================================
    // Types
    // ====================================================

    gridTypes: {
        name: 'gridTypes',
        postData: {data: 'types'},
        columns: [
            {field: 'recid', caption: 'Value', size: '50px', resizable: true, sortable: true},
            {field: 'name_type', caption: 'Type', size: '100px', resizable: true, sortable: true},
            {field: 'description', caption: 'Description', size: '300px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'Value', type: 'int'},
            {field: 'name_type', caption: 'Type', type: 'text'},
            {field: 'description', caption: 'Description', type: 'text'}
        ]
    },

    formTypes: {
        name: 'formTypes',
        postData: {data: 'types'},
        fields: [
            {
                field: 'recid', type: 'int', required: true, html: {caption: 'Value: '},
                options: {autoFormat: false}
            },
            {field: 'name_type', type: 'text', required: true, html: {caption: 'Type: '}},
            {field: 'description', type: 'text', required: true, html: {caption: 'Description: '}}
        ]
    },

    // ====================================================
    // Users
    // ====================================================

    gridUsers: {
        name: 'gridUsers',
        postData: {data: 'users'},
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'user', caption: 'User', size: '100px', resizable: true, sortable: true},
            {field: 'name', caption: 'Name', size: '150px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'user', caption: 'User', type: 'text'},
            {field: 'name', caption: 'Name', type: 'text'}
        ]
    },

    formUsers: {
        name: 'formUsers',
        postData: {data: 'users'},
        fields: [
            {field: 'user', type: 'text', required: true, html: {caption: 'User: '}},
            {field: 'name', type: 'text', required: true, html: {caption: 'Name: '}},
            {field: 'password', type: 'password', required: true, html: {caption: 'Password: '}}
        ]
    },

    // ====================================================
    // Views
    // ====================================================

    gridViews: {
        name: 'gridViews',
        postData: {data: 'views'},
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'view', caption: 'View', size: '100px', resizable: true, sortable: true},
            {field: 'rule', caption: 'Rule', size: '300px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'view', caption: 'View', type: 'text'},
            {field: 'rule', caption: 'Rule', type: 'text'}
        ]
    },

    formViews: {
        name: 'formViews',
        postData: {data: 'views'},
        fields: [
            {field: 'view', type: 'text', required: true, html: {caption: 'View: '}},
            {field: 'rule', type: 'text', required: true, html: {caption: 'Rule: '}}
        ]
    }
};

// Apply the shared configuration to every editable PowerGSLB entity, keyed by base name.
['Domains', 'Monitors', 'Records', 'Routings', 'Types', 'Users', 'Views'].forEach(
    function (base) {
        var grid = config['grid' + base];
        grid.show = {
            footer: true,
            selectColumn: true,
            toolbar: true,
            toolbarReload: true,
            toolbarColumns: true,
            toolbarSearch: true,
            toolbarAdd: true,
            toolbarEdit: true,
            toolbarDelete: true
        };
        grid.sortData = [{field: 'recid', direction: 'asc'}];
        grid.url = w2uiUrl;
        grid.toolbar = {items: [{type: 'spacer'}, themeToolbarItem()], onClick: themeToolbarClick};
        grid.onAdd = gridPopupForm;
        grid.onDblClick = gridPopupForm;
        grid.onEdit = gridPopupForm;

        var form = config['form' + base];
        form.actions = {
            'Close': function () {
                w2popup.close();
            },
            'Save': function () {
                if (this.validate().length === 0) {
                    this.save();
                    w2popup.close();
                }
            }
        };
        form.style = 'border: 0px; background-color: transparent';
        form.url = w2uiUrl;
        form.onSave = function () {
            w2ui['grid' + base].reload();
        };
    }
);

// ====================================================
// Widget initialization
// ====================================================

$(function () {
    // on page initialization
    w2obj.grid.prototype.buttons.add.caption = 'Add';
    $('#powergslb').w2layout(config.layout);
    w2ui.layout.content('left', $().w2sidebar(config.sidebar));
    w2ui.layout.content('main', $().w2grid(config.gridStatus));

    // in memory initialization
    $().w2grid(config.gridDomains);
    $().w2grid(config.gridMonitors);
    $().w2grid(config.gridRecords);
    $().w2grid(config.gridRoutings);
    $().w2grid(config.gridTypes);
    $().w2grid(config.gridUsers);
    $().w2grid(config.gridViews);
    $().w2form(config.formDomains);
    $().w2form(config.formMonitors);
    $().w2form(config.formRecords);
    $().w2form(config.formRoutings);
    $().w2form(config.formTypes);
    $().w2form(config.formViews);
    $().w2form(config.formUsers);
});
