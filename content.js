// Exemple d'extraction de l'adresse de l'expéditeur dans le DOM de Gmail
function getSenderEmail() {
  // Sélectionne l'élément contenant l'adresse e-mail dans l'interface Gmail
  const senderElement = document.querySelector('span[email]');
  if (senderElement) {
    const email = senderElement.getAttribute('email');
    console.log("Mail adress found:", email);
    return email;
  }
  return null;
}

// Écoute les messages envoyés par le pop-up de l'extension
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "ANALYZE_MAIL") {
    const senderEmail = getSenderEmail();
    sendResponse({ email: senderEmail });
  }
});