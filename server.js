const express = require('express');
const path = require('path');
const fs = require('fs');
const app = express();
const PORT = process.env.PORT || 3000;

const FILE_PATH = path.join(__dirname, 'registros.json');

// Middleware para entender datos en formato JSON
app.use(express.json());
app.use(express.static(__dirname));

// Función auxiliar para leer el archivo JSON de forma segura
function leerRegistros() {
    try {
        if (!fs.existsSync(FILE_PATH)) return [];
        const contenido = fs.readFileSync(FILE_PATH, 'utf-8');
        return contenido ? JSON.parse(contenido) : [];
    } catch (error) {
        console.error("Error al leer el archivo de registros:", error);
        return [];
    }
}

// RUTA 1: Recibir una nueva fichada desde el celular del guardia
app.post('/api/fichar', (req, res) => {
    const nuevoRegistro = req.body;
    if (!nuevoRegistro.guardia || !nuevoRegistro.punto) {
        return res.status(400).json({ error: "Datos incompletos" });
    }

    const historial = leerRegistros();
    historial.unshift(nuevoRegistro); // Agregar al principio

    try {
        fs.writeFileSync(FILE_PATH, JSON.stringify(historial, null, 2), 'utf-8');
        return res.json({ success: true, mensaje: "Fichada guardada en el servidor" });
    } catch (error) {
        console.error("Error al escribir el archivo de registros:", error);
        return res.status(500).json({ error: "Error interno al guardar" });
    }
});

// RUTA 2: Entregar el historial completo para la pestaña de Reportes/Excel
app.get('/api/historial', (req, res) => {
    res.json(leerRegistros());
});

// RUTA 3: Borrar el historial
app.delete('/api/historial', (req, res) => {
    try {
        fs.writeFileSync(FILE_PATH, JSON.stringify([], null, 2), 'utf-8');
        res.json({ success: true });
    } catch (error) {
        res.status(500).json({ error: "No se pudo borrar el historial" });
    }
});

// Servir la interfaz web principal
app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, () => {
    console.log(`Servidor de Rondines corriendo en el puerto ${PORT}`);
});
