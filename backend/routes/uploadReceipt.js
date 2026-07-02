const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs');

const upload = multer({
  storage: multer.diskStorage({
    destination: (req, file, cb) => {
      const uploadDir = path.join(__dirname, '..', 'uploads');
      fs.mkdirSync(uploadDir, { recursive: true });
      cb(null, uploadDir);
    },
    filename: (req, file, cb) => {
      const safeName = `${Date.now()}-${file.originalname.replace(/[^a-zA-Z0-9.-]/g, '_')}`;
      cb(null, safeName);
    }
  }),
});

module.exports = () => {
  const router = express.Router();

  router.post('/upload-receipt', upload.single('file'), (req, res) => {
    if (!req.file) {
      return res.status(400).json({ error: 'Receipt file is required.' });
    }
    const fileUrl = `/api/uploads/${req.file.filename}`;
    res.json({ url: fileUrl });
  });

  router.use('/uploads', express.static(path.join(__dirname, '..', 'uploads')));

  return router;
};
