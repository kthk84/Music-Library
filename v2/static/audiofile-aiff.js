/**
 * Minimal AIFF decoder (from audiofile.js style) that works with ArrayBuffer.
 * Use: decodeAIFFFromArrayBuffer(arrayBuffer) -> { channels, length, sampleRate, bitDepth }
 */
(function (global) {
    'use strict';

    function readString(data, offset, length) {
        return String.fromCharCode.apply(null, data.subarray(offset, offset + length));
    }

    function readIntB(data, offset, length) {
        var value = 0;
        for (var i = 0; i < length; i++) {
            value = value + (data[offset + i] * Math.pow(2, 8 * (length - i - 1)));
        }
        return value;
    }

    function readChunkHeaderB(data, offset) {
        return {
            name: readString(data, offset, 4),
            length: readIntB(data, offset + 4, 4)
        };
    }

    function readFloatB(data, offset) {
        var expon = (data[offset] << 8) | data[offset + 1];
        if (expon >= 32768) expon -= 65536;
        var sign = 1;
        if (expon < 0) {
            sign = -1;
            expon += 32768;
        }
        var himant = readIntB(data, offset + 2, 4);
        var lomant = readIntB(data, offset + 6, 4);
        var value;
        if (expon === 0 && himant === 0 && lomant === 0) {
            value = 0;
        } else if (expon === 0x7FFF) {
            value = Number.MAX_VALUE;
        } else {
            expon -= 16383;
            value = (himant * 0x100000000 + lomant) * Math.pow(2, expon - 63);
        }
        return sign * value;
    }

    function decodeAIFFFromArrayBuffer(arrayBuffer) {
        var data = new Uint8Array(arrayBuffer);
        var decoded = {};
        var offset = 0;
        var chunk = readChunkHeaderB(data, offset);
        offset += 8;
        if (chunk.name !== 'FORM') return null;
        var fileLength = chunk.length + 8;
        var aiff = readString(data, offset, 4);
        offset += 4;
        if (aiff !== 'AIFF') return null;
        var numberOfChannels, length, bitDepth, bytesPerSample, sampleRate, channels;
        while (offset < fileLength) {
            chunk = readChunkHeaderB(data, offset);
            offset += 8;
            if (chunk.name === 'COMM') {
                numberOfChannels = readIntB(data, offset, 2);
                offset += 2;
                length = readIntB(data, offset, 4);
                offset += 4;
                channels = [];
                for (var c = 0; c < numberOfChannels; c++) {
                    channels.push(new Float32Array(length));
                }
                bitDepth = readIntB(data, offset, 2);
                bytesPerSample = bitDepth / 8;
                offset += 2;
                sampleRate = readFloatB(data, offset);
                offset += 10;
            } else if (chunk.name === 'SSND') {
                var dataOffset = readIntB(data, offset, 4);
                offset += 4;
                offset += 4;
                offset += dataOffset;
                var range = 1 << (bitDepth - 1);
                for (var i = 0; i < numberOfChannels; i++) {
                    var channel = channels[i];
                    for (var j = 0; j < length; j++) {
                        var idx = offset + (j * numberOfChannels + i) * bytesPerSample;
                        var value = readIntB(data, idx, bytesPerSample);
                        if (value >= range) value -= (range * 2);
                        channel[j] = value / range;
                    }
                }
                offset += chunk.length - dataOffset - 8;
            } else {
                offset += chunk.length;
            }
        }
        decoded.sampleRate = sampleRate;
        decoded.bitDepth = bitDepth;
        decoded.channels = channels;
        decoded.length = length;
        return decoded;
    }

    global.decodeAIFFFromArrayBuffer = decodeAIFFFromArrayBuffer;
})(typeof window !== 'undefined' ? window : this);
