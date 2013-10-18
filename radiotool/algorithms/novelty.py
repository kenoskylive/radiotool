# Compute the audio novelty features of a song

import sys

import numpy as N
import scipy.spatial.distance as scidist
import scipy.signal

from ..composer.utils import RMS_energy

def novelty(song, k=64, wlen_ms=100, duration=None, start=0, nchangepoints=5, feature="rms"):
    if feature != "rms":
        raise ValueError, "novelty currently only supports 'rms' feature"

    frames = song.all_as_mono()

    wlen_samples = wlen_ms * song.samplerate / 1000

    if duration is None:
        frames = frames[start * song.samplerate:]
    else:
        frames = frames[start * song.samplerate:(start + duration) *
             song.samplerate]
             
    # Compute energies
    hamming = N.hamming(wlen_samples)
    nwindows = int(2 * song.total_frames() / wlen_samples - 1)
    energies = N.empty(nwindows)
    for i in range(nwindows):
        energies[i] = RMS_energy(
            hamming *
            frames[i * wlen_samples / 2:
                   i * wlen_samples / 2 + wlen_samples]
        )

    energies_list = [[x] for x in energies]
    
    # Compute similarities
    S_matrix = 1 - scidist.squareform(
                    scidist.pdist(energies_list, 'euclidean'))
                    
    # smooth the C matrix with a gaussian taper
    C_matrix = N.kron(N.eye(2), N.ones((k,k))) -\
               N.kron([[0, 1], [1, 0]], N.ones((k,k)))
    g = scipy.signal.gaussian(2*k, k)
    C_matrix = N.multiply(C_matrix, N.multiply.outer(g.T, g))
    
    # Created checkerboard kernel
    
    N_vec = N.zeros(N.shape(S_matrix)[0])
    for i in xrange(k, len(N_vec) - k):
        S_part = S_matrix[i - k:i + k, i - k:i + k]
        N_vec[i] = N.sum(N.multiply(S_part, C_matrix))
        
    # Computed checkerboard response

    peaks = naive_peaks(N_vec)
    out_peaks = []
    
    # ensure that the points we return are more exciting
    # after the change point than before the change point
    for p in peaks:
        frame = p[0]
        if frame > k:
            left_frames = frames[(frame - k) * wlen_samples / 2:
                                 frame * wlen_samples / 2]
            right_frames = frames[frame * wlen_samples / 2:
                                  (frame + k) * wlen_samples / 2]
            if RMS_energy(left_frames) <\
               RMS_energy(right_frames):
               out_peaks.append(p)

    out_peaks = [(x[0] * wlen_ms / 2000.0, x[1]) for x in out_peaks]
    for i, p in enumerate(out_peaks):
        if i == nchangepoints:
            break
    
    return [x[0] for x in out_peaks[:nchangepoints]]


def smooth_hanning(x, size=11):
    """smooth a 1D array using a hanning window with requested size."""

    if x.ndim != 1:
        raise ValueError, "smooth_hanning only accepts 1-D arrays."
    if x.size < size:
        raise ValueError, "Input vector needs to be bigger than window size."
    if size < 3:
        return x

    s = N.r_[x[size - 1:0:-1], x, x[-1:-size:-1]]
    w = N.hanning(size)
    y = N.convolve(w / w.sum(), s, mode='valid')
    return y


def naive_peaks(vec):
    """A naive method for finding peaks of a signal.
    1. Smooth vector
    2. Find peaks (local maxima)
    3. Find local max from original signal, pre-smoothing
    4. Return (sorted, descending) peaks
    """
    a = smooth_hanning(vec, 33)
    peaks = N.r_[True, a[1:] > a[:-1]] & N.r_[a[:-1] > a[1:], True]

    p = N.array(N.where(peaks)[0])
    maxidx = N.zeros(N.shape(p))
    maxvals = N.zeros(N.shape(p))
    for i, pk in enumerate(p):
        maxidx[i] = N.argmax(vec[pk - 16:pk + 16]) + pk - 16
        maxvals[i] = N.max(vec[pk - 16:pk + 16])
    out = N.array([maxidx, maxvals]).T
    return out[(-out[:, 1]).argsort()]

if __name__ == '__main__':
    novelty(sys.argv[1], k=int(sys.argv[2]))