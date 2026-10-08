var exec = require('cordova/exec');

function call(action, args) {
    return new Promise(function (resolve, reject) {
        exec(resolve, reject, 'BmsUiBundle', action, args || []);
    });
}

module.exports = {
    getStatus: function () {
        return call('getStatus');
    },
    installBundle: function (descriptor, files) {
        return call('installBundle', [descriptor || {}, Array.isArray(files) ? files : []]);
    },
    clearBundle: function () {
        return call('clearBundle');
    }
};
