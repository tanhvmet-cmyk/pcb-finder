"""Group copies of the same letter shape (used to let all copies of a letter vote on what it is)."""
import numpy as np, cv2
GW, GH = 20, 28

def _dt(img): return cv2.distanceTransform((img == 0).astype(np.uint8), cv2.DIST_L2, 3)
def _cham(a, da, b, db):
    pa, pb = a > 0, b > 0
    if not pa.any() or not pb.any(): return 9.0
    return (db[pa].mean() + da[pb].mean()) / 2

def cluster(imgs, thr=0.28):
    reps, ids = [], []
    for im in imgs:
        d = _dt(im); best, bd = -1, 9
        for k, (r, rd) in enumerate(reps):
            v = _cham(im, d, r, rd)
            if v < bd: bd, best = v, k
        if bd <= thr: ids.append(best)
        else: reps.append((im, d)); ids.append(len(reps) - 1)
    return reps, ids

