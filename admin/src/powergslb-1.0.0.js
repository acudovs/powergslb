// ====================================================
// Widget configuration
// ====================================================

var w2uiUrl = '/admin/w2ui';

var gridShow = {
    footer: true,
    lineNumbers: false,
    selectColumn: true,
    toolbar: true,
    toolbarReload: true,
    toolbarColumns: true,
    toolbarSearch: true,
    toolbarAdd: true,
    toolbarEdit: true,
    toolbarDelete: true
};

var gridSortData = [
    {field: 'recid', direction: 'asc'}
];

var gridPopupForm = (function (event) {
    switch (event.target) {
        case 'gridDomains':
            openPopupForm(event, 'domain', 400, 175, 'formDomains');
            break;
        case 'gridMonitors':
            openPopupForm(event, 'monitor', 400, 210, 'formMonitors');
            break;
        case 'gridRecords':
            openPopupForm(event, 'record', 400, 600, 'formRecords');
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
        case 'gridLBMethods':
          openPopupForm(event, 'lbmethod', 400, 210, 'formLBMethods');
          break;
        case 'gridLBOptions':
          openPopupForm(event, 'lboption', 400, 210, 'formLBOptions');
          break;
    }
});

var openPopupForm = (function (event, record, popup_width, popup_height, form_name) {
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
            if (sel.length == 1) {
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
});

var formActions = {
    'Close': function () {
        w2popup.close();
    },
    'Save': function () {
        if (this.validate() == 0) {
            this.save();
            w2popup.close();
        }
    }
};

var formStyle = 'border: 0px; background-color: transparent';

var panelStyle = 'border: 1px solid #dfdfdf; padding: 5px;';

// Get statistics
var get_stats = (
  function(recid, record, method) {
    if ( method == 'pool' ) {
      api_method = 'get-stats-pool';
      div_name = '_pool';
      graph_title = 'Pool ' + record.name;
    } else {
      api_method = 'get-stats';
      div_name = '';
      graph_title = record.content;
    }

    $.ajax({
      type: "POST",
      url: "/admin/w2ui",
      dataType: 'json',
      data: { "cmd": api_method, "recid": recid, "data": "status" },
      complete: function( data ) {
        var chart = c3.generate({
          bindto: '#status' + div_name,
          data: {
            x: 'ts',
            columns: data.responseJSON,
            color: function(color,d) {
              if ( method != 'pool' ) {
                return d.value == 1 ? 'green' : 'red';
              } else {
                return color;
              }
            }
            //           colors: {
            //             'y': function(d) { return d.value == 1 ? 'green' : 'red'; }
            //           }
          },
          axis: {
            y: {
              label: {
                text: 'Availability',
                position: 'outer-middle'
              }
            },
            x: {
              type: 'timeseries',
              label: {
                text: 'Time (GMT)',
                position: 'outer-middle'
              },
              tick: {
                //               format: '%H:%M:%S'
                format: function (x) {
                  var t = new Date( x );
                  var formatted = t.getHours() + ':' + t.getMinutes() + ':' + t.getSeconds();
                  return formatted;
                },
                count : 10
              }
            },
          },
          legend: {
            show: true,
          },
          title: {
            text: graph_title + ' - Availability over Time'
          }
        });
      }
    });

    $.ajax({
      type: "POST",
      url: "/admin/w2ui",
      dataType: 'json',
      data: { "cmd": api_method, "recid": recid, "data": "rt" },
      complete: function( data ) {
        var chart = c3.generate({
          bindto: '#response_time' + div_name,
          data: {
            x: 'ts',
            columns: data.responseJSON
          },
          axis: {
            y: {
              label: {
                text: 'Check Response',
                position: 'outer-middle'
              },
            },
            x: {
              type: 'timeseries',
              label: {
                text: 'Time (GMT)',
                position: 'outer-middle'
              },
              tick: {
                //               format: '%H:%M:%S'
                format: function (x) {
                  var t = new Date( x );
                  var formatted = t.getHours() + ':' + t.getMinutes() + ':' + t.getSeconds();
                  return formatted;
                },
                count : 10
              }
            },
          },
          legend: {
            show: true,
          },
          title: {
            text: graph_title + ' - Check Data over Time'
          }
        });
      }
    });
  }
);

var openPopupStatus = (function (event, recid, popup_width, popup_height, plot_name) {

//   console.log('recid: '+recid+' - event.target: '+event.target)

  var record = w2ui['gridStatus'].get(recid);
//   console.log('recid: '+recid+' - event.target: '+event.target + ' - record: ' + record + ' - ' + record.content)

  w2popup.open({
    title: record.content + ' - Statistics',
    body: '<div id="myTabs"></div><div id="selected-tab" style="padding: 10px 0px">tab1</div>',
    style: 'padding: 15px 0px 0px 0px',
    width: popup_width,
    height: popup_height,
    onClose: function (event) {
      w2ui['myTabs'].destroy();
    }
  });

  $('#myTabs').w2tabs({
    name     : 'myTabs',
    active   : 'tab1',
    tabs    : [
    { id: 'tab1', caption: record.content },
    { id: 'tab2', caption: 'Pool ' + record.name },
    ],
    onRender: function (event) {
//       console.log( event );
//       if ( event.target == 'tab1' ) {
        $('#selected-tab').html('<div id="status" style="width: 90%; height: 25%;"></div><div id="response_time" style="width: 90%; height: 25%;"></div>');
        get_stats( recid, record, '' );
//       } else {
//         $('#selected-tab').html('<div id="status_pool" style="width: 90%; height: 25%;"></div><div id="response_time_pool" style="width: 90%; height: 25%;"></div>');
//         get_stats( recid, record, 'pool' );
//       }
    },
    onClick: function (event) {
      if ( event.target == 'tab1' ) {
        $('#selected-tab').html('<div id="status" style="width: 90%; height: 25%;"></div><div id="response_time" style="width: 90%; height: 25%;"></div>');
        get_stats( recid, record, '' );
      } else {
        $('#selected-tab').html('<div id="status_pool" style="width: 90%; height: 25%;"></div><div id="response_time_pool" style="width: 90%; height: 25%;"></div>');
        get_stats( recid, record, 'pool' );
      }
    },
  });


});

var reloadInterval = 3000;
var reloadIntervalId = 0;

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
                    {id: 'gridTypes', text: 'Types', img: 'icon-page'},
                    {id: 'gridViews', text: 'Views', img: 'icon-page'}
                ]
            },
            {
              id: 'lb', text: 'LB', expanded: true, group: true,
              nodes: [
                {id: 'gridLBMethods', text: 'Methods', img: 'icon-page'},
                {id: 'gridLBOptions', text: 'Options', img: 'icon-page'}
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
                case 'gridStatus':
                case 'gridDomains':
                case 'gridMonitors':
                case 'gridRecords':
                case 'gridTypes':
                case 'gridUsers':
                case 'gridViews':
                case 'gridLBMethods':
                case 'gridLBOptions':
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
        show: gridShow,
        sortData: gridSortData,
        postData: {data: 'status'},
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'status', caption: 'Status', size: '55px', resizable: true, sortable: true},
            {field: 'domain', caption: 'Domain', size: '100px', resizable: true, sortable: true},
            {field: 'name', caption: 'Name', size: '150px', resizable: true, sortable: true},
            {field: 'name_type', caption: 'Type', size: '60px', resizable: true, sortable: true},
            {field: 'content', caption: 'Content', size: '510px', resizable: true, sortable: true},
            {field: 'ttl', caption: 'TTL', size: '55px', resizable: true, sortable: true},
            {field: 'disabled', caption: 'Disabled', size: '65px', resizable: true, sortable: true},
            {field: 'fallback', caption: 'Fallback', size: '60px', resizable: true, sortable: true},
            {field: 'persistence', caption: 'Persistence', size: '80px', resizable: true, sortable: true},
            {field: 'weight', caption: 'Weight', size: '55px', resizable: true, sortable: true},
            {field: 'lbmethod', caption: 'LB Method', size: '70px', resizable: true, sortable: true},
            {field: 'lboption', caption: 'LB Opts', size: '70px', resizable: true, sortable: true},
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
            {field: 'fallback', caption: 'Fallback', type: 'int'},
            {field: 'persistence', caption: 'Persistence', type: 'int'},
            {field: 'weight', caption: 'Weight', type: 'int'},
            {field: 'lbmethod', caption: 'LB Method', type: 'text'},
            {field: 'lboption', caption: 'LB Opts', type: 'text'},
            {field: 'monitor', caption: 'Monitor', type: 'text'},
            {field: 'view', caption: 'View', type: 'text'}
        ],
        show: {
            footer: true,
            lineNumbers: true,
            toolbar: true,
            toolbarReload: true,
            toolbarColumns: true,
            toolbarSearch: true
        },
        sortData: [
            {field: 'domain', direction: 'asc'},
            {field: 'status', direction: 'asc'}
        ],
        toolbar: {
            items: [
                {id: 'break', type: 'break'},
                {
                    id: 'reload', type: 'check', caption: 'Auto Reload', icon: 'w2ui-icon-reload',
                    checked: false, hint: 'Auto reload data in the list'
                }
            ],
            onClick: function (event) {
                if (event.target == 'reload') {
                    if (event.object.checked == false) {
                        reloadIntervalId = setInterval(function () {
                            w2ui.gridStatus.reload();
                        }, reloadInterval);
                        w2ui.gridStatus.reload();
                    } else {
                        clearInterval(reloadIntervalId);
                        reloadIntervalId = 0;
                    }
                }
            }
        },
        onClick: function (event) {
          event.onComplete = function () {
            var grid = w2ui[event.target];
            var sel = grid.getSelection();
//             console.log('selection:', sel);
            if ( $.isArray(sel) && sel.length == 1 && $.isNumeric(sel[0]) ) {
              openPopupStatus(event, sel[0], 800, 800, 'rt,status');
            }
          }
        },
    },

    // ====================================================
    // Domains
    // ====================================================

    gridDomains: {
        name: 'gridDomains',
        postData: {data: 'domains'},
        show: gridShow,
        sortData: gridSortData,
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'domain', caption: 'Domain', size: '100px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'domain', caption: 'Domain', type: 'text'}
        ],
        onAdd: gridPopupForm,
        onDblClick: gridPopupForm,
        onEdit: gridPopupForm
    },

    formDomains: {
        name: 'formDomains',
        postData: {data: 'domains'},
        actions: formActions,
        style: formStyle,
        url: w2uiUrl,
        fields: [
            {field: 'domain', type: 'text', required: true, html: {caption: 'Domain: '}}
        ],
        onSave: function () {
            w2ui.gridDomains.reload();
        }
    },

    // ====================================================
    // Monitors
    // ====================================================

    gridMonitors: {
        name: 'gridMonitors',
        postData: {data: 'monitors'},
        show: gridShow,
        sortData: gridSortData,
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'monitor', caption: 'Monitor', size: '150px', resizable: true, sortable: true},
            {field: 'monitor_json', caption: 'Monitor JSON', size: '750px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'monitor', caption: 'Monitor', type: 'text'},
            {field: 'monitor_json', caption: 'Monitor JSON', type: 'text'}
        ],
        onAdd: gridPopupForm,
        onDblClick: gridPopupForm,
        onEdit: gridPopupForm
    },

    formMonitors: {
        name: 'formMonitors',
        postData: {data: 'monitors'},
        actions: formActions,
        style: formStyle,
        url: w2uiUrl,
        fields: [
            {field: 'monitor', type: 'text', required: true, html: {caption: 'Monitor: '}},
            {field: 'monitor_json', type: 'text', required: true, html: {caption: 'Monitor JSON: '}}
        ],
        onSave: function () {
            w2ui.gridMonitors.reload();
        }
    },

    // ====================================================
    // Records
    // ====================================================

    gridRecords: {
        name: 'gridRecords',
        postData: {data: 'records'},
        show: gridShow,
        sortData: gridSortData,
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'domain', caption: 'Domain', size: '100px', resizable: true, sortable: true},
            {field: 'name', caption: 'Name', size: '150px', resizable: true, sortable: true},
            {field: 'name_type', caption: 'Type', size: '60px', resizable: true, sortable: true},
            {field: 'content', caption: 'Content', size: '510px', resizable: true, sortable: true},
            {field: 'ttl', caption: 'TTL', size: '55px', resizable: true, sortable: true},
            {field: 'disabled', caption: 'Disabled', size: '65px', resizable: true, sortable: true},
            {field: 'fallback', caption: 'Fallback', size: '60px', resizable: true, sortable: true},
            {field: 'persistence', caption: 'Persistence', size: '80px', resizable: true, sortable: true},
            {field: 'weight', caption: 'Weight', size: '55px', resizable: true, sortable: true},
            {field: 'lbmethod', caption: 'LB Method', size: '55px', resizable: true, sortable: true},
            {field: 'lboption', caption: 'LB Option', size: '55px', resizable: true, sortable: true},
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
            {field: 'fallback', caption: 'Fallback', type: 'int'},
            {field: 'persistence', caption: 'Persistence', type: 'int'},
            {field: 'weight', caption: 'Weight', type: 'int'},
            {field: 'lbmethod', caption: 'LB Method', type: 'text'},
            {field: 'lboption', caption: 'LB Option', type: 'text'},
            {field: 'monitor', caption: 'Monitor', type: 'text'},
            {field: 'view', caption: 'View', type: 'text'}
        ],
        onAdd: gridPopupForm,
        onDblClick: gridPopupForm,
        onEdit: gridPopupForm
    },

    formRecords: {
        name: 'formRecords',
        postData: {data: 'records'},
        actions: formActions,
        style: formStyle,
        url: w2uiUrl,
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
            {
                field: 'disabled', type: 'int', required: false, html: {caption: 'Disabled: '},
                options: {autoFormat: false}
            },
            {
                field: 'fallback', type: 'int', required: false, html: {caption: 'Fallback: '},
                options: {autoFormat: false}
            },
            {
                field: 'persistence', type: 'int', required: false, html: {caption: 'Persistence: '},
                options: {autoFormat: false}
            },
            {
                field: 'weight', type: 'int', required: false, html: {caption: 'Weight: '},
                options: {autoFormat: false}
            },
            {
              field: 'lbmethod', type: 'combo', required: false, html: {caption: 'LB Method: '},
              options: {
                postData: {'cmd': 'get-items', data: 'lbmethods', field: 'lbmethod'},
                placeholder: 'Type to search...', match: 'contains', url: w2uiUrl
              }
            },
            {
              field: 'lboption', type: 'combo', required: false, html: {caption: 'LB Options: '},
              options: {
                postData: {'cmd': 'get-items', data: 'lboptions', field: 'lboption'},
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
        ],
        onSave: function () {
            w2ui.gridRecords.reload();
        }
    },

    // ====================================================
    // Types
    // ====================================================

    gridTypes: {
        name: 'gridTypes',
        postData: {data: 'types'},
        show: gridShow,
        sortData: gridSortData,
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'Value', size: '50px', resizable: true, sortable: true},
            {field: 'name_type', caption: 'Type', size: '100px', resizable: true, sortable: true},
            {field: 'description', caption: 'Description', size: '150px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'Value', type: 'int'},
            {field: 'name_type', caption: 'Type', type: 'text'},
            {field: 'description', caption: 'Description', type: 'text'}
        ],
        onAdd: gridPopupForm,
        onDblClick: gridPopupForm,
        onEdit: gridPopupForm
    },

    formTypes: {
        name: 'formTypes',
        postData: {data: 'types'},
        actions: formActions,
        style: formStyle,
        url: w2uiUrl,
        fields: [
            {
                field: 'recid', type: 'int', required: true, html: {caption: 'Value: '},
                options: {autoFormat: false}
            },
            {field: 'name_type', type: 'text', required: true, html: {caption: 'Type: '}},
            {field: 'description', type: 'text', required: true, html: {caption: 'Description: '}}
        ],
        onSave: function () {
            w2ui.gridTypes.reload();
        }
    },

    // ====================================================
    // Users
    // ====================================================

    gridUsers: {
        name: 'gridUsers',
        postData: {data: 'users'},
        show: gridShow,
        sortData: gridSortData,
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'user', caption: 'User', size: '100px', resizable: true, sortable: true},
            {field: 'name', caption: 'Name', size: '150px', resizable: true, sortable: true},
            {field: 'password', caption: 'Password', size: '325px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'user', caption: 'User', type: 'text'},
            {field: 'name', caption: 'Name', type: 'text'},
            {field: 'password', caption: 'Password', type: 'text'}
        ],
        onAdd: gridPopupForm,
        onDblClick: gridPopupForm,
        onEdit: gridPopupForm
    },

    formUsers: {
        name: 'formUsers',
        postData: {data: 'users'},
        actions: formActions,
        style: formStyle,
        url: w2uiUrl,
        fields: [
            {field: 'user', type: 'text', required: true, html: {caption: 'User: '}},
            {field: 'name', type: 'text', required: true, html: {caption: 'Name: '}},
            {field: 'password', type: 'password', required: true, html: {caption: 'Password: '}}
        ],
        onSave: function () {
            w2ui.gridUsers.reload();
        }
    },

    // ====================================================
    // Views
    // ====================================================

    gridViews: {
        name: 'gridViews',
        postData: {data: 'views'},
        show: gridShow,
        sortData: gridSortData,
        url: w2uiUrl,
        columns: [
            {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
            {field: 'view', caption: 'View', size: '100px', resizable: true, sortable: true},
            {field: 'rule', caption: 'Rule', size: '300px', resizable: true, sortable: true}
        ],
        searches: [
            {field: 'recid', caption: 'ID', type: 'int'},
            {field: 'view', caption: 'View', type: 'text'},
            {field: 'rule', caption: 'Rule', type: 'text'}
        ],
        onAdd: gridPopupForm,
        onDblClick: gridPopupForm,
        onEdit: gridPopupForm
    },

    formViews: {
        name: 'formViews',
        postData: {data: 'views'},
        actions: formActions,
        style: formStyle,
        url: w2uiUrl,
        fields: [
            {field: 'view', type: 'text', required: true, html: {caption: 'View: '}},
            {field: 'rule', type: 'text', required: true, html: {caption: 'Rule: '}}
        ],
        onSave: function () {
            w2ui.gridViews.reload();
        }
    },

    // ====================================================
    // LBMethods
    // ====================================================

    gridLBMethods: {
      name: 'gridLBMethods',
      postData: {data: 'lbmethods'},
      show: gridShow,
      sortData: gridSortData,
      url: w2uiUrl,
      columns: [
      {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
      {field: 'lbmethod', caption: 'Method', size: '100px', resizable: true, sortable: true},
      {field: 'lbmethod_description', caption: 'Description', size: '300px', resizable: true, sortable: true}
      ],
      searches: [
      {field: 'recid', caption: 'ID', type: 'int'},
      {field: 'lbmethod', caption: 'Method', type: 'text'},
      {field: 'lbmethod_description', caption: 'Description', type: 'text'}
      ],
      onAdd: gridPopupForm,
      onDblClick: gridPopupForm,
      onEdit: gridPopupForm
    },

    formLBMethods: {
      name: 'formLBMethods',
      postData: {data: 'lbmethods'},
      actions: formActions,
      style: formStyle,
      url: w2uiUrl,
      fields: [
      {field: 'lbmethod', type: 'text', required: true, html: {caption: 'Method: '}},
      {field: 'lbmethod_description', type: 'text', required: true, html: {caption: 'Description: '}}
      ],
      onSave: function () {
        w2ui.gridLBMethods.reload();
      }
    },

    // ====================================================
    // LBOptions
    // ====================================================

    gridLBOptions: {
      name: 'gridLBOptions',
      postData: {data: 'lboptions'},
      show: gridShow,
      sortData: gridSortData,
      url: w2uiUrl,
      columns: [
      {field: 'recid', caption: 'ID', size: '50px', resizable: true, sortable: true},
      {field: 'lbmethod', caption: 'Method', size: '100px', resizable: true, sortable: true},
      {field: 'lboption', caption: 'Name', size: '150px', resizable: true, sortable: true},
      {field: 'lboption_json', caption: 'Configuration', size: '300px', resizable: true, sortable: true}
      ],
      searches: [
      {field: 'recid', caption: 'ID', type: 'int'},
      {field: 'lbmethod', caption: 'Method', type: 'text'},
      {field: 'lboption', caption: 'Name', type: 'text'},
      {field: 'lboption_json', caption: 'Configuration', type: 'text'}
      ],
      onAdd: gridPopupForm,
      onDblClick: gridPopupForm,
      onEdit: gridPopupForm
    },

    formLBOptions: {
      name: 'formLBOptions',
      postData: {data: 'lboptions'},
      actions: formActions,
      style: formStyle,
      url: w2uiUrl,
      focus: 1,
      fields: [
      {
        field: 'lbmethod', type: 'combo', required: true, html: {caption: 'Method: '},
        options: {
          postData: {'cmd': 'get-items', data: 'lbmethods', field: 'lbmethod'},
          placeholder: 'Type to search...', match: 'contains', url: w2uiUrl
        }
      },
      {field: 'lboption', type: 'text', required: true, html: {caption: 'Name: '}},
      {field: 'lboption_json', type: 'text', required: true, html: {caption: 'Configuration: '}}
      ],
      onSave: function () {
        w2ui.gridLBOptions.reload();
      }
    },

};

// ====================================================
// Widget initialization
// ====================================================

$(function () {
    // on page initialization
    $('#powergslb').w2layout(config.layout);
    w2ui.layout.content('left', $().w2sidebar(config.sidebar));
    w2ui.layout.content('main', $().w2grid(config.gridStatus));

    // in memory initialization
    $().w2grid(config.gridDomains);
    $().w2grid(config.gridMonitors);
    w2ui['gridStatus'].hideColumn('recid');
    $().w2grid(config.gridRecords);
    $().w2grid(config.gridTypes);
    $().w2grid(config.gridUsers);
    $().w2grid(config.gridViews);
    $().w2grid(config.gridLBMethods);
    $().w2grid(config.gridLBOptions);

    $().w2form(config.formDomains);
    $().w2form(config.formMonitors);
    $().w2form(config.formRecords);
    $().w2form(config.formTypes);
    $().w2form(config.formViews);
    $().w2form(config.formUsers);
    $().w2form(config.formLBMethods);
    $().w2form(config.formLBOptions);
});
