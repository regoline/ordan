document.querySelector('input[name="fediverse_identifier"]').addEventListener('input', function(e) {
    const value = e.target.value;
    const isValid = value.includes('@') || 
                   value.includes('.') || 
                   value.startsWith('http');
    
    e.target.classList.toggle('invalid', !isValid);
});
