// Example of extracting the sender's email address from the Gmail DOM
function getSenderEmail() {
  // Select the span element that contains the email address of the sender
  const senderElement = document.querySelector('span[email]');
  if (senderElement) {
    const email = senderElement.getAttribute('email');
    console.log("Mail adress found:", email);
    return email;
  }
  return null;
}

// content.js : extract the email body from the Gmail interface
function getEmailBody() {
  const bodyElement = document.querySelector('div[role="main"] .a3s');
  return bodyElement ? bodyElement.innerText : "";
}

// Listen for messages from the popup script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "ANALYZE_MAIL") {
    sendResponse({ 
      email: getSenderEmail(), 
      text: getEmailBody() 
    });
  }
});