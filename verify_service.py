from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/verify', methods=['POST'])
def verify():
    data = request.get_json(force=True)
    bank = data.get('bank')
    reference = data.get('reference')

    if not bank or not reference:
        return jsonify({'success': False, 'error': 'Missing bank or reference'}), 400

    # Example verification logic.
    # Replace this with actual integration to your Python microservice / receipt extraction.
    verified_amount = None
    payer_name = None
    notes = 'Verification logic is not implemented yet.'
    success = False

    # Mocked verification flow for demo purposes. Replace with real extractor call.
    if reference.startswith('FT') or reference.startswith('CHQ'):
        verified_amount = 3600
        payer_name = 'Dawit Bekele'
        success = True
        notes = 'Mocked verification pass.'

    return jsonify({
        'success': success,
        'amount': verified_amount,
        'payer_name': payer_name,
        'notes': notes,
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
