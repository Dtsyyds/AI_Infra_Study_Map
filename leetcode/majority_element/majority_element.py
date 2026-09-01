def majority_element_hash(nums: list[int]) -> int:
    hash_map = {}
    for num in nums:
        if num in hash_map:
            hash_map[num] += 1
        else:
            hash_map[num] = 1

        if hash_map[num] > len(nums) // 2:
            return num

    return 0


def majority_element_boyer_moore(nums: list[int]) -> int:
    votes = 0
    candidate = None
    for num in nums:
        if votes == 0:
            candidate = num
        if num == candidate:
            votes += 1
            if votes > len(nums) // 2:
                return candidate
        else:
            votes -= 1
    return candidate
