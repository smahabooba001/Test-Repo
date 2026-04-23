#include <stdio.h>

int main() {
    int value1, value2, sum;

    printf("Enter first value: ");
    if (scanf("%d", &value1) != 1) {
        printf("Invalid input.\n");
        return 1;
    }

    printf("Enter second value: ");
    if (scanf("%d", &value2) != 1) {
        printf("Invalid input.\n");
        return 1;
    }

    sum = value1 + value2;
    printf("Sum: %d\n", sum);

    return 0;
}
