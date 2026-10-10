var checkout = {};

$(document).ready(function() {
  var $messages = $('.messages-content'),
    d, h, m,
    i = 0;

  function startChat() {
    insertResponseMessage('Hi there, I\'m your Dining Concierge. How can I help?');
  }

  if (document.readyState === 'complete') {
    startChat();
  } else {
    $(window).on('load', startChat);
  }

  function sessionId() {
    var key = 'diningConciergeSessionId';
    var id = window.localStorage.getItem(key);
    if (!id) {
      id = 'user-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
      window.localStorage.setItem(key, id);
    }
    return id;
  }

  function escapeHtml(text) {
    return $('<div>').text(text == null ? '' : String(text)).html();
  }

  function updateScrollbar() {
    var pane = $messages.closest('.messages').get(0);
    if (pane) {
      pane.scrollTop = pane.scrollHeight;
    }
  }

  function setDate() {
    d = new Date()
    if (m != d.getMinutes()) {
      m = d.getMinutes();
      $('<div class="timestamp">' + d.getHours() + ':' + m + '</div>').appendTo($('.message:last'));
    }
  }

  function callChatbotApi(message) {
    // params, body, additionalParams
    return sdk.chatbotPost({}, {
      messages: [{
        type: 'unstructured',
        unstructured: {
          id: sessionId(),
          text: message,
          timestamp: new Date().toISOString()
        }
      }]
    }, {});
  }

  function insertMessage() {
    msg = $('.message-input').val();
    if ($.trim(msg) == '') {
      return false;
    }
    $('<div class="message message-personal">' + escapeHtml(msg) + '</div>').appendTo($messages).addClass('new');
    setDate();
    $('.message-input').val(null);
    updateScrollbar();

    callChatbotApi(msg)
      .then((response) => {
        console.log(response);
        var data = response.data;

        if (data.messages && data.messages.length > 0) {
          console.log('received ' + data.messages.length + ' messages');

          var messages = data.messages;

          for (var message of messages) {
            if (message.type === 'unstructured') {
              insertResponseMessage(escapeHtml(message.unstructured.text));
            } else if (message.type === 'structured' && message.structured.type === 'product') {
              var html = '';

              insertResponseMessage(message.structured.text);

              setTimeout(function() {
                html = '<img src="' + message.structured.payload.imageUrl + '" witdth="200" height="240" class="thumbnail" /><b>' +
                  message.structured.payload.name + '<br>$' +
                  message.structured.payload.price +
                  '</b><br><a href="#" onclick="' + message.structured.payload.clickAction + '()">' +
                  message.structured.payload.buttonLabel + '</a>';
                insertResponseMessage(html);
              }, 1100);
            } else {
              console.log('not implemented');
            }
          }
        } else {
          insertResponseMessage('Oops, something went wrong. Please try again.');
        }
      })
      .catch((error) => {
        console.log('an error occurred', error);
        insertResponseMessage('Oops, something went wrong. Please try again.');
      });
  }

  $('.message-submit').click(function() {
    insertMessage();
  });

  $(window).on('keydown', function(e) {
    if (e.which == 13) {
      insertMessage();
      return false;
    }
  })

  function insertResponseMessage(content) {
    $('<div class="message loading new"><figure class="avatar"><img src="https://media.tenor.com/images/4c347ea7198af12fd0a66790515f958f/tenor.gif" /></figure><span></span></div>').appendTo($messages);
    updateScrollbar();

    setTimeout(function() {
      $('.message.loading').remove();
      $('<div class="message new"><figure class="avatar"><img src="https://media.tenor.com/images/4c347ea7198af12fd0a66790515f958f/tenor.gif" alt="" /></figure>' + content + '</div>').appendTo($messages).addClass('new');
      setDate();
      updateScrollbar();
      i++;
    }, 500);
  }

});
