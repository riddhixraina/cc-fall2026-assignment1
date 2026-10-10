(function () {
  var hosted = "https://REPLACE_ME.execute-api.us-east-1.amazonaws.com/v1";
  var host = window.location.hostname;
  var local = host === "localhost" || host === "127.0.0.1";
  window.DINING_API_BASE = local ? window.location.origin + "/v1" : hosted;
})();
