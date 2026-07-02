const express = require('express');

const planDurations = {
  'Basic Monthly': 30,
  'Student Plan': 30,
  'Standard Quarterly': 90,
  'Premium Annual': 365,
};

function addDays(dateString, days) {
  const date = new Date(dateString);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function findMemberByPhone(db, phone) {
  return new Promise((resolve, reject) => {
    db.get('SELECT * FROM members WHERE phone = ?', [phone], (err, row) => {
      if (err) return reject(err);
      resolve(row);
    });
  });
}

function createGuestMember(db, payload) {
  return new Promise((resolve, reject) => {
    const id = `GUEST-${Date.now()}`;
    const registrationDate = new Date().toISOString().slice(0, 10);
    db.run(
      `INSERT INTO members (id, name, phone, status, plan, registration_date, expiry_date, last_updated)
       VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
      [
        id,
        payload.customer_name || 'Guest',
        payload.customer_phone || '',
        'pending',
        payload.plan || 'Unknown',
        registrationDate,
        addDays(registrationDate, planDurations[payload.plan] || 30),
      ],
      function (err) {
        if (err) return reject(err);
        resolve({ id, plan: payload.plan, status: 'pending', expiry_date: addDays(registrationDate, planDurations[payload.plan] || 30) });
      }
    );
  });
}

function insertPayment(db, payment) {
  return new Promise((resolve, reject) => {
    db.run(
      `INSERT INTO payments (member_id, payment_date, amount, method, receipt, status)
       VALUES (?, ?, ?, ?, ?, ?)`,
      [
        payment.member_id,
        payment.payment_date,
        payment.amount,
        payment.method,
        payment.receipt,
        payment.status,
      ],
      function (err) {
        if (err) return reject(err);
        resolve(this.lastID);
      }
    );
  });
}

function updatePaymentStatus(db, paymentId, status) {
  return new Promise((resolve, reject) => {
    db.run('UPDATE payments SET status = ? WHERE id = ?', [status, paymentId], (err) => {
      if (err) return reject(err);
      resolve();
    });
  });
}

function updateMemberMembership(db, memberId, plan) {
  return new Promise((resolve, reject) => {
    db.get('SELECT expiry_date, status FROM members WHERE id = ?', [memberId], (err, member) => {
      if (err) return reject(err);
      if (!member) return reject(new Error('Member not found'));
      const days = planDurations[plan] || 30;
      const now = new Date();
      const expiryBase = member.expiry_date && new Date(member.expiry_date) > now ? member.expiry_date : now.toISOString().slice(0, 10);
      const nextExpiry = addDays(expiryBase, days);
      db.run(
        'UPDATE members SET status = ?, expiry_date = ?, plan = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?',
        ['active', nextExpiry, plan, memberId],
        (err) => {
          if (err) return reject(err);
          resolve(nextExpiry);
        }
      );
    });
  });
}

module.exports = (db) => {
  const router = express.Router();

  router.post('/submit-payment', async (req, res) => {
    const payload = req.body || {};
    const required = ['plan', 'expected_amount', 'bank_code', 'reference', 'account_holder_name', 'customer_name'];
    for (const key of required) {
      if (!payload[key]) {
        return res.status(400).json({ error: `Missing required field: ${key}` });
      }
    }

    try {
      let member = null;
      if (payload.member_id) {
        member = await new Promise((resolve, reject) => {
          db.get('SELECT * FROM members WHERE id = ?', [payload.member_id], (err, row) => {
            if (err) return reject(err);
            resolve(row);
          });
        });
      }

      const phone = payload.customer_phone || '';
      if (!member && phone) {
        member = await findMemberByPhone(db, phone);
      }

      if (!member) {
        member = await createGuestMember(db, payload);
      }

      const paymentDate = new Date().toISOString().slice(0, 10);
      const paymentId = await insertPayment(db, {
        member_id: member.id,
        payment_date: paymentDate,
        amount: payload.expected_amount,
        method: payload.bank_code,
        receipt: payload.screenshot_url || payload.reference,
        status: 'pending_verification',
      });

      const verifyUrl = process.env.VERIFY_SERVICE_URL || 'http://localhost:5000/verify';
      const verifyResponse = await fetch(verifyUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bank: payload.bank_code, reference: payload.reference, member_id: member.id }),
      });

      if (!verifyResponse.ok) {
        await updatePaymentStatus(db, paymentId, 'needs_review');
        return res.status(202).json({ status: 'needs_review', message: 'Verification service is unavailable.' });
      }

      const verifyResult = await verifyResponse.json();
      const amountMatch = verifyResult.amount === payload.expected_amount;
      const payerMatch = verifyResult.payer_name && payload.customer_name && verifyResult.payer_name.toLowerCase().includes(payload.customer_name.toLowerCase());
      const isVerified = verifyResult.success && amountMatch && payerMatch;
      const finalStatus = isVerified ? 'paid' : 'needs_review';

      await updatePaymentStatus(db, paymentId, finalStatus);
      if (isVerified) {
        await updateMemberMembership(db, member.id, payload.plan);
      }

      return res.status(200).json({
        payment_id: paymentId,
        status: finalStatus,
        verified_amount: verifyResult.amount,
        verified_payer_name: verifyResult.payer_name,
        notes: verifyResult.notes || null,
      });
    } catch (error) {
      console.error('submit-payment error:', error);
      return res.status(500).json({ error: error.message || 'Unable to submit payment.' });
    }
  });

  return router;
};
