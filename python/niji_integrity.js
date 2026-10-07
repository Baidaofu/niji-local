// Frida agent that runs inside the official niji・journey app process and
// provides Play Integrity tokens plus the app-scoped Android ID.
//
// Loaded together with java_bridge.js (frida-java-bridge bundled by esbuild).
'use strict';

var CLOUD_PROJECT = 624942467596; // NijiIntegrityModule GOOGLE_CLOUD_PROJECT_NUMBER
var provider = null;
var seq = 0;

function uniq(prefix) { return 'com.nijilocal.' + prefix + (++seq) + '_' + Math.floor(Math.random() * 1e7); }

function appContext() {
  var AT = Java.use('android.app.ActivityThread');
  return AT.currentApplication().getApplicationContext();
}

function keep(cls) {
  globalThis.__niji_keep = globalThis.__niji_keep || [];
  globalThis.__niji_keep.push(cls);
  return cls.$new();
}

function onSuccess(handler) {
  var OnSuccess = Java.use('com.google.android.gms.tasks.OnSuccessListener');
  var cls = Java.registerClass({
    name: uniq('S'),
    implements: [OnSuccess],
    methods: {
      onSuccess: function (v) {
        try { handler(v); }
        catch (e) { send({ tag: 'integrity', text: 'onSuccess handler threw: ' + e }); }
      }
    }
  });
  return keep(cls);
}

function onFailure(reject, tag) {
  var OnFailure = Java.use('com.google.android.gms.tasks.OnFailureListener');
  var cls = Java.registerClass({
    name: uniq('F'),
    implements: [OnFailure],
    methods: {
      onFailure: function (e) {
        var msg;
        try { msg = String(e); } catch (err) { msg = '<unprintable>'; }
        send({ tag: 'integrity', text: tag + ' failed: ' + msg });
        reject(new Error(tag + ': ' + msg));
      }
    }
  });
  return keep(cls);
}

function stdToken(nonce) {
  return new Promise(function (resolve, reject) {
    Java.perform(function () {
      try {
        if (!provider) { reject(new Error('provider not prepared')); return; }
        var _p = provider;
        var B = Java.use('com.google.android.play.core.integrity.StandardIntegrityManager$StandardIntegrityTokenRequest').builder();
        B.setRequestHash(nonce);
        send({ tag: 'integrity', text: 'standard: requestHash length=' + String(nonce).length });
        _p.request(B.build())
          .addOnSuccessListener(onSuccess(function (v) {
            try {
              var Tok = Java.use('com.google.android.play.core.integrity.StandardIntegrityManager$StandardIntegrityToken');
              var t = String(Java.cast(Java.retain(v), Tok).token());
              send({ tag: 'integrity', text: 'standard: token length=' + t.length });
              resolve(t);
            } catch (ex) { reject(new Error('token(): ' + ex)); }
          }))
          .addOnFailureListener(onFailure(reject, 'standard'));
      } catch (e) { reject(new Error('standard-sync: ' + e)); }
    });
  });
}

function classicToken(nonce, cloud) {
  return new Promise(function (resolve, reject) {
    Java.perform(function () {
      try {
        var Factory = Java.use('com.google.android.play.core.integrity.IntegrityManagerFactory');
        var mgr = Factory.create(appContext());
        var Base64 = Java.use('android.util.Base64');
        var b64 = String(Base64.encodeToString(Base64.decode(nonce, 0), 11)); // NO_WRAP
        var B = Java.use('com.google.android.play.core.integrity.IntegrityTokenRequest').builder();
        B.setNonce(b64);
        B.setCloudProjectNumber(cloud);
        send({ tag: 'integrity', text: 'classic: nonce length=' + b64.length });
        mgr.requestIntegrityToken(B.build())
          .addOnSuccessListener(onSuccess(function (v) {
            try {
              var Resp = Java.use('com.google.android.play.core.integrity.IntegrityTokenResponse');
              var t = String(Java.cast(Java.retain(v), Resp).token());
              send({ tag: 'integrity', text: 'classic: token length=' + t.length });
              resolve(t);
            } catch (ex) { reject(new Error('token(): ' + ex)); }
          }))
          .addOnFailureListener(onFailure(reject, 'classic'));
      } catch (e) { reject(new Error('classic-sync: ' + e)); }
    });
  });
}

rpc.exports = {
  deviceid: function () {
    var S = Java.use('android.provider.Settings$Secure');
    return String(S.getString(appContext().getContentResolver(), 'android_id'));
  },

  prepare: function (cloud) {
    cloud = cloud || CLOUD_PROJECT;
    return new Promise(function (resolve, reject) {
      Java.perform(function () {
        try {
          var Factory = Java.use('com.google.android.play.core.integrity.IntegrityManagerFactory');
          var mgr = Factory.createStandard(appContext());
          var B = Java.use('com.google.android.play.core.integrity.StandardIntegrityManager$PrepareIntegrityTokenRequest').builder();
          B.setCloudProjectNumber(cloud);
          send({ tag: 'integrity', text: 'prepare: cloudProjectNumber=' + cloud });
          mgr.prepareIntegrityToken(B.build())
            .addOnSuccessListener(onSuccess(function (v) {
              var Iface = Java.use('com.google.android.play.core.integrity.StandardIntegrityManager$StandardIntegrityTokenProvider');
              provider = Java.cast(Java.retain(v), Iface);
              globalThis.__niji_provider = provider;
              send({ tag: 'integrity', text: 'prepare ok' });
              resolve(true);
            }))
            .addOnFailureListener(onFailure(reject, 'prepare'));
        } catch (e) { reject(new Error('prepare-sync: ' + e)); }
      });
    });
  },

  standard: stdToken,

  classic: function (nonce, cloud) { return classicToken(nonce, cloud || CLOUD_PROJECT); },

  token: function (nonce, cloud) {
    cloud = cloud || CLOUD_PROJECT;
    var prep = provider ? Promise.resolve(true) : rpc.exports.prepare(cloud);
    return prep.then(function () { return stdToken(nonce); })
      .catch(function (e) {
        send({ tag: 'integrity', text: 'standard path failed (' + e + '), falling back to classic' });
        return classicToken(nonce, cloud);
      });
  }
};
